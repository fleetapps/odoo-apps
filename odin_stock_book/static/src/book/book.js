import {
    Component, onMounted, onPatched, onWillStart, onWillUnmount, toRaw, useExternalListener, useRef, useState,
} from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { debounce } from "@web/core/utils/timing";
import { download } from "@web/core/network/download";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { parseAmount, parseDay } from "./utils";
import { PurchaseDialog } from "./purchase_dialog";

const WITH_PARTNER = new Set(["sale", "return", "received"]);
// The order Enter moves through the entry row.
const NEW_COLS = ["date", "item", "qty_in", "qty_out", "reason", "partner_name"];
// How long the row just saved stays highlighted, and an Undo stays offered.
const FLASH_MS = 1600;
const UNDO_MS = 8000;
// Lines posted per request: each batch is saved before the next, so a long bulk
// post cannot run out of server time and lose all of it.
const POST_BATCH = 25;

function blankRow(date, keep = {}) {
    return { date, product: null, query: "", qty_in: "", qty_out: "", reason: keep.reason || "", partner_name: "" };
}

/** Nothing typed in the entry row yet: its date can follow the month tab. */
function untouched(row) {
    return !row.product && !row.query && !row.qty_in && !row.qty_out && !row.partner_name;
}

function nextFrame() {
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

/** The wording for one, or for several. */
const few = (n, one, many) => (n === 1 ? one : many);

export class StockBook extends Component {
    static template = "odin_stock_book.Book";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.root = useRef("root");
        const params = this.props.action.params || {};
        this.state = useState({
            view: params.view || "log", loading: true, book: null, sheet: null, count: null, recipes: null, picked: {},
            year: false, month: false, reason: "", status: "", search: "", group: "",
            selectedId: null, open: {}, newRow: null, suggest: null, saving: false, documents: {},
            compSearch: { query: "", results: [] }, confirmCount: false, countConflicts: null,
            warehouseId: params.warehouse_id || false, warehouses: [], pasting: false,
            // Crispness: what is being saved, what is being switched to, and
            // which rows to highlight for a moment.
            pending: 0, saveNote: "", refreshing: 0, flash: {}, showCancelled: false,
        });
        // Each load has a number; a load that comes back after a newer one
        // started is dropped, so a slow answer never puts old values back.
        this.loadSeq = 0;
        this.timers = new Set();
        this.patchWaiters = [];
        this.suggestItems = debounce(this.suggestItems.bind(this), 180);
        this.suggestPartners = debounce(this.suggestPartners.bind(this), 180);
        this.searchComponents = debounce(this.searchComponents.bind(this), 180);
        this.reloadSoon = debounce(() => this.load(), 200);
        onWillStart(() => this.load());
        onMounted(() => {
            if (this.state.view === "log") {
                this.scrollToEnd();
                this.focusNew("item");
            }
        });
        onPatched(() => {
            const waiters = this.patchWaiters;
            this.patchWaiters = [];
            waiters.forEach((resolve) => resolve());
        });
        onWillUnmount(() => this.timers.forEach((t) => clearTimeout(t)));
        useExternalListener(window, "keydown", (ev) => this.onGlobalKey(ev));
    }

    later(fn, ms) {
        const timer = setTimeout(() => {
            this.timers.delete(timer);
            fn();
        }, ms);
        this.timers.add(timer);
    }

    /** Resolves once the page shows the latest state. */
    rendered() {
        return Promise.race([new Promise((resolve) => this.patchWaiters.push(resolve)), nextFrame()]);
    }

    get sheetEl() {
        return this.root.el?.querySelector(".o_sb_sheet");
    }

    // ------------------------------------------------------------ data
    async load() {
        const seq = ++this.loadSeq;
        const { year, month, search } = this.state;
        const warehouse_id = this.state.warehouseId;
        const view = this.state.view;
        let data;
        if (view === "log") {
            data = await this.orm.call("odin.stock.book", "get_book", [], {
                year, month, reason: this.state.reason, status: this.state.status, search, warehouse_id,
            });
        } else if (view === "sheet") {
            data = await this.orm.call("odin.stock.book", "get_sheet", [], {
                year, month, group: this.state.group, search, warehouse_id,
            });
        } else if (view === "recipes") {
            data = await this.orm.call("odin.stock.book", "get_recipes", [], {
                group: this.state.group, search, warehouse_id,
            });
        } else {
            data = await this.orm.call("odin.stock.book", "get_count", [], {
                group: this.state.group, search, warehouse_id,
            });
        }
        if (seq !== this.loadSeq || view !== this.state.view) {
            return false;
        }
        const sheet = this.sheetEl;
        const atEnd = sheet && sheet.scrollHeight - sheet.scrollTop - sheet.clientHeight < 24;
        // The month first: the entry row's date follows it.
        if (data.year) {
            this.state.year = data.year;
            this.state.month = data.month;
        }
        if (view === "log") {
            this.applyBook(data);
        } else if (view === "sheet") {
            this.state.sheet = data;
        } else if (view === "recipes") {
            this.state.recipes = data;
        } else {
            this.state.count = data;
        }
        if (data.warehouse) {
            this.state.warehouseId = data.warehouse.id;
            this.state.warehouses = data.warehouses;
        }
        this.state.loading = false;
        if (this.state.selectedId && !this.selected) {
            this.state.selectedId = null;
        }
        // A line posted while selected now prints its documents.
        const selected = this.selected;
        if (selected && selected.state === "posted" && !this.state.documents[selected.id]?.length) {
            this.loadDocuments(selected.id);
        }
        if (atEnd && view === "log") {
            // Reading the newest lines, like a chat: stay at the end.
            this.rendered().then(() => this.scrollToEnd());
        }
        return true;
    }

    /**
     * The month's rows, keeping the rows that did not change as they were,
     * so the page only touches what changed; a row's components stay open or
     * closed as they were first shown.
     */
    applyBook(book) {
        if (!book.empty) {
            const before = new Map((toRaw(this.state.book)?.rows || []).map((r) => [r.id, r]));
            book.rows = book.rows.map((row) => {
                const old = before.get(row.id);
                return old && JSON.stringify(old) === JSON.stringify(row) ? old : row;
            });
            for (const row of book.rows) {
                if (this.state.open[row.id] === undefined && row.components.length) {
                    this.state.open[row.id] = this.defaultOpen(row);
                }
            }
        }
        this.state.book = book;
        if (book.empty) {
            return;
        }
        const row = this.state.newRow;
        if (!row) {
            this.state.newRow = blankRow(this.defaultDate());
        } else if (untouched(row) && !row.date.startsWith(this.monthPrefix)) {
            row.date = this.defaultDate();
        }
    }

    /**
     * Changes made on the page: the call, then the month again. "Saving…"
     * shows meanwhile and "Saved" after; on an error the page reloads so it
     * never shows what the server refused.
     */
    async mutate(call, { flash = null, reveal = false, onResult = null } = {}) {
        this.state.pending++;
        this.state.saveNote = "saving";
        let result;
        try {
            result = await call();
        } catch (error) {
            this.state.pending--;
            if (!this.state.pending) {
                this.state.saveNote = "";
            }
            await this.load();
            throw error;
        }
        this.state.pending--;
        // Before the month comes back, so it shows in its final form at once.
        onResult?.(result);
        await this.load();
        const ids = typeof flash === "function" ? flash(result) : flash;
        if (ids?.length) {
            this.flashRows(ids, reveal);
        }
        if (!this.state.pending) {
            this.state.saveNote = "saved";
            this.later(() => !this.state.pending && (this.state.saveNote = ""), 1500);
        }
        return result;
    }

    /** Highlight rows for a moment; ``reveal``: scroll the first into view. */
    async flashRows(ids, reveal = false) {
        for (const id of ids) {
            this.state.flash[id] = (this.state.flash[id] || 0) + 1;
        }
        this.later(() => ids.forEach((id) => delete this.state.flash[id]), FLASH_MS);
        if (reveal) {
            await this.rendered();
            this.root.el?.querySelector(`.o_sb_row[data-id="${ids[0]}"]`)?.scrollIntoView({ block: "nearest" });
        }
    }

    scrollToEnd() {
        const sheet = this.sheetEl;
        if (sheet) {
            sheet.scrollTop = sheet.scrollHeight;
        }
    }

    /** Switching month, year, warehouse or view: the old content stays,
     * dimmed, until the new one is there; no blank page in between. */
    async navigate(change, { toEnd = true } = {}) {
        this.state.refreshing++;
        change();
        try {
            if (await this.load()) {
                await this.rendered();
                if (toEnd && this.state.view === "log") {
                    this.scrollToEnd();
                }
            }
        } finally {
            this.state.refreshing--;
        }
    }

    /**
     * A notice with Undo. ``undo`` runs at most once; the notice closes
     * when it is clicked.
     */
    notifyUndo(message, undo, options = {}) {
        let done = false;
        const close = this.notification.add(message, {
            type: "info", autocloseDelay: UNDO_MS, ...options,
            buttons: [{
                name: _t("Undo"), primary: true, icon: "fa-undo",
                onClick: async () => {
                    if (done) {
                        return;
                    }
                    done = true;
                    close();
                    await undo();
                },
            }],
        });
        return close;
    }

    get allRows() {
        return this.state.book?.rows || [];
    }

    /** Cancelled lines are left out unless asked for. */
    get rows() {
        return this.state.showCancelled ? this.allRows : this.allRows.filter((r) => r.state !== "cancelled");
    }

    get cancelledCount() {
        return this.allRows.filter((r) => r.state === "cancelled").length;
    }

    get selected() {
        return this.rows.find((r) => r.id === this.state.selectedId);
    }

    /** Rows with a day heading before each new date, like the sheet's blocks. */
    get dayRows() {
        const out = [];
        let last = null;
        for (const row of this.rows) {
            if (row.date !== last) {
                out.push({ day: row.date, key: `d${row.date}` });
                last = row.date;
            }
            out.push({ row, key: `r${row.id}` });
        }
        return out;
    }

    get monthLabel() {
        const months = (this.state.book || this.state.sheet)?.months;
        if (!months) {
            return "";
        }
        // In sentences: "Nov 2026". The tabs keep the short capitals.
        const label = months[this.state.month - 1].label;
        return `${label[0]}${label.slice(1).toLowerCase()} ${this.state.year}`;
    }

    get monthPrefix() {
        return `${this.state.year}-${String(this.state.month).padStart(2, "0")}`;
    }

    get readyIds() {
        return this.rows.filter((r) => r.status_key === "ready").map((r) => r.id);
    }

    defaultDate() {
        const book = this.state.book;
        const [y, m] = book.today.split("-").map(Number);
        return y === book.year && m === book.month ? book.today
            : `${book.year}-${String(book.month).padStart(2, "0")}-01`;
    }

    fmt(value, digits = 0) {
        if (value === "" || value === null || value === undefined) {
            return "";
        }
        return Number(value).toLocaleString("en-KE", { minimumFractionDigits: digits, maximumFractionDigits: 2 });
    }

    money(value) {
        if (value === "" || value === null || value === undefined) {
            return "";
        }
        return Number(value).toLocaleString("en-KE", { maximumFractionDigits: 0 });
    }

    shortDate(value) {
        if (!value) {
            return "";
        }
        const [y, m, d] = value.split("-").map(Number);
        const label = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1];
        return `${d} ${label}`;
    }

    dayHeading(value) {
        const [y, m, d] = value.split("-").map(Number);
        const day = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
        return `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][day]} ${this.shortDate(value)}`;
    }

    shortRef(ref) {
        if (!ref) {
            return "";
        }
        const parts = ref.split("/");
        return `${parts[0]}/${parts[parts.length - 1]}`;
    }

    /** The quantity column the reason does not use: hidden until hovered or focused. */
    quiet(direction, column, value) {
        return !value && ((column === "in" && direction === "out") || (column === "out" && direction === "in"));
    }

    // ------------------------------------------------------------ navigation
    setView(view) {
        return this.navigate(() => {
            this.state.view = view;
            this.state.selectedId = null;
            this.state.group = "";
            this.state.search = "";
        }).then(() => view === "log" && this.focusNew("item"));
    }

    /** The entry row is hidden under a filter: a new movement clears it. */
    async startNew() {
        if (this.state.view !== "log") {
            await this.setView("log");
        } else if (this.state.status || this.state.search) {
            this.state.status = "";
            this.state.search = "";
            await this.load();
            await this.rendered();
        }
        this.scrollToEnd();
        this.focusNew("item");
    }

    setWarehouse(ev) {
        return this.navigate(() => {
            this.state.warehouseId = Number(ev.target.value) || false;
            this.state.selectedId = null;
            // Its on-hand figures belong to the other warehouse.
            this.state.newRow = null;
            this.state.countConflicts = null;
        });
    }

    setMonth(month) {
        return this.navigate(() => {
            this.state.month = month;
            this.state.selectedId = null;
        });
    }

    shiftYear(delta) {
        return this.navigate(() => {
            this.state.year += delta;
            this.state.selectedId = null;
        });
    }

    async setReason(reason) {
        this.state.reason = reason;
        await this.load();
    }

    async setStatus(status) {
        this.state.status = this.state.status === status ? "" : status;
        if (this.state.view !== "log") {
            await this.setView("log");
        } else {
            await this.load();
        }
    }

    async setGroup(group) {
        this.state.group = this.state.group === group ? "" : group;
        await this.load();
    }

    onSearch(ev) {
        this.state.search = ev.target.value;
        this.reloadSoon();
    }

    openOlder() {
        const older = this.state.book.older_open;
        return this.navigate(() => {
            this.state.year = older.year;
            this.state.month = older.month;
            this.state.selectedId = null;
        });
    }

    toggleCancelled() {
        this.state.showCancelled = !this.state.showCancelled;
        if (!this.state.showCancelled && this.state.selectedId && !this.selected) {
            this.state.selectedId = null;
        }
    }

    select(row) {
        this.state.selectedId = this.state.selectedId === row.id ? null : row.id;
        this.state.compSearch = { query: "", results: [] };
        if (this.state.selectedId && row.state === "posted") {
            this.loadDocuments(row.id);
        }
    }

    // ------------------------------------------------------------ documents
    /** The documents a posted line prints (Business Documents installed):
     * its delivery note or GRN, invoice, purchase order. */
    async loadDocuments(lineId) {
        const documents = await this.orm.call("odin.stock.line", "book_documents", [[lineId]]);
        this.state.documents = { ...this.state.documents, [lineId]: documents };
        return documents;
    }

    async printDocument(lineId, doc) {
        const action = await this.orm.call("odin.stock.line", "book_print_document",
            [[lineId], doc.report_id, doc.res_model, doc.res_id]);
        await this.action.doAction(action);
    }

    /** Print buttons for a notification, after a sale or purchase posts. */
    async printButtons(lineId) {
        const documents = lineId ? await this.loadDocuments(lineId) : [];
        return documents.map((doc) => ({
            name: _t("Print %s", doc.label),
            onClick: () => this.printDocument(lineId, doc),
        }));
    }

    toggle(row) {
        this.state.open[row.id] = !this.isOpen(row);
    }

    /** Open by default only when a component is short, so a batch with a
     * dozen ingredients does not push the rest of the day off the screen. */
    defaultOpen(row) {
        return row.state === "draft"
            && row.components.some((c) => !c.unlimited && c.onhand_after !== null && c.onhand_after < 0);
    }

    isOpen(row) {
        const flag = this.state.open[row.id];
        return flag === undefined ? this.defaultOpen(row) : flag;
    }

    onRowClick(ev, row) {
        if (ev.target.closest("input, select, button, .o_sb_suggest")) {
            return;
        }
        if (ev.target.closest(".o_sb_toggle") && row.components.length) {
            this.toggle(row);
            return;
        }
        this.select(row);
    }

    /** Like a spreadsheet cell: clicking in selects what is there, so typing replaces it. */
    onCellFocus(ev) {
        ev.target.select?.();
    }

    // ------------------------------------------------------------ editing rows
    async save(row, values) {
        const saved = await this.mutate(() => this.orm.call("odin.stock.book", "book_save", [row.id, values]),
            { flash: [row.id] });
        this.tellMoved(saved);
        return saved;
    }

    /** The reason only removes (or only adds): say where the number went, and offer to put it back. */
    tellMoved(saved) {
        if (!saved.moved_to) {
            return;
        }
        const toRemoved = saved.moved_to === "removed";
        const message = toRemoved
            ? _t("%(reason)s removes stock: the quantity is in Removed.", { reason: saved.reason_label })
            : _t("%(reason)s adds stock: the quantity is in Added.", { reason: saved.reason_label });
        this.notifyUndo(message, () => {
            const qty = toRemoved ? saved.qty_out : saved.qty_in;
            const values = toRemoved ? { qty_in: qty, qty_out: 0 } : { qty_in: 0, qty_out: qty };
            return this.mutate(() => this.orm.call("odin.stock.book", "book_save", [saved.id, values],
                { keep_column: true }), { flash: [saved.id] });
        });
    }

    onCell(ev, row, field) {
        let value = ev.target.value;
        if (field === "qty_in" || field === "qty_out") {
            value = parseAmount(value, NaN);
            if (Number.isNaN(value) || value < 0) {
                this.notification.add(_t("Type a quantity, e.g. 240."), { type: "warning" });
                ev.target.value = row[field] ? this.fmt(row[field]) : "";
                return;
            }
        } else if (field === "date") {
            value = parseDay(value, this.state.year, this.state.month);
            if (!value) {
                this.notification.add(_t("Type a day like 3, 3/11 or 3 Nov."), { type: "warning" });
                ev.target.value = this.shortDate(row.date);
                return;
            }
        } else if (field === "bom_id") {
            value = Number(value) || false;
        }
        if (String(row[field] ?? "") === String(value ?? "")) {
            return;
        }
        this.save(row, { [field]: value });
    }

    setUnitCost(row, ev) {
        const cost = parseAmount(ev.target.value, NaN);
        if (Number.isNaN(cost) || cost < 0) {
            this.notification.add(_t("Type the cost per unit, e.g. 19."), { type: "warning" });
            return;
        }
        this.save(row, { unit_cost: cost });
    }

    setComponent(row, comp, ev) {
        const qty = parseAmount(ev.target.value, NaN);
        if (Number.isNaN(qty) || qty < 0) {
            this.notification.add(_t("Type a quantity, e.g. 240."), { type: "warning" });
            ev.target.value = this.fmt(comp.qty);
            return;
        }
        return this.mutate(() => this.orm.call("odin.stock.book", "book_component", [row.id],
            { component_id: comp.id, qty }), { flash: [row.id] });
    }

    removeComponent(row, comp) {
        return this.mutate(() => this.orm.call("odin.stock.book", "book_component", [row.id],
            { component_id: comp.id, remove: true }), { flash: [row.id] });
    }

    async resetComponents(row) {
        await this.mutate(() => this.orm.call("odin.stock.book", "book_reset_components", [row.id]),
            { flash: [row.id] });
        this.notification.add(_t("Components reset to the recipe."), { type: "info" });
    }

    onCompSearch(ev) {
        this.state.compSearch.query = ev.target.value;
        this.searchComponents();
    }

    async searchComponents() {
        const q = this.state.compSearch.query;
        this.state.compSearch.results = q ? await this.orm.call("odin.stock.book", "book_suggest_products", [q],
            { warehouse_id: this.state.warehouseId }) : [];
    }

    addComponent(row, product) {
        this.state.compSearch = { query: "", results: [] };
        this.state.open[row.id] = true;
        return this.mutate(() => this.orm.call("odin.stock.book", "book_component", [row.id],
            { product_id: product.id, qty: 1 }), { flash: [row.id] });
    }

    // ------------------------------------------------------------ the entry row
    onNewInput(field, ev) {
        const row = this.state.newRow;
        if (field === "item") {
            row.query = ev.target.value;
            row.product = null;
            this.suggestItems(ev.target.value);
        } else {
            row[field] = ev.target.value;
            if (field === "partner_name") {
                this.suggestPartners(ev.target.value);
            }
        }
    }

    async suggestItems(text) {
        if (!text) {
            this.state.suggest = null;
            return;
        }
        const items = await this.orm.call("odin.stock.book", "book_suggest_products", [text],
            { warehouse_id: this.state.warehouseId });
        // Typed on since: a newer answer is on its way.
        if (this.state.newRow?.query !== text) {
            return;
        }
        this.state.suggest = items.length ? { field: "item", items, index: 0 } : null;
    }

    async suggestPartners(text) {
        if (!text || text.length < 2) {
            this.state.suggest = null;
            return;
        }
        const items = await this.orm.call("odin.stock.book", "book_suggest_partners", [text]);
        if (this.state.newRow?.partner_name !== text) {
            return;
        }
        this.state.suggest = items.length ? { field: "partner_name", items, index: 0 } : null;
    }

    pickItem(item) {
        const row = this.state.newRow;
        row.product = item;
        row.query = item.display_name;
        if (!row.reason) {
            row.reason = item.usual_reason;
        }
        this.state.suggest = null;
        const direction = this.reasonDirection(row.reason);
        this.focusNew(direction === "out" ? "qty_out" : "qty_in");
    }

    pickSuggestion(item) {
        const s = this.state.suggest;
        if (s?.field === "item") {
            this.pickItem(item);
        } else if (s?.field === "partner_name") {
            this.state.newRow.partner_name = item;
            this.state.suggest = null;
            this.focusNew("partner_name");
        }
    }

    reasonDirection(reason) {
        return this.state.book.reasons.find((r) => r.key === reason)?.direction || "both";
    }

    onNewKeydown(ev, field) {
        const s = this.state.suggest;
        if (s && s.field === field) {
            if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                ev.preventDefault();
                const n = s.items.length;
                s.index = (s.index + (ev.key === "ArrowDown" ? 1 : n - 1)) % n;
                return;
            }
            if (ev.key === "Enter" || (ev.key === "Tab" && !ev.shiftKey)) {
                ev.preventDefault();
                this.pickSuggestion(s.items[s.index]);
                return;
            }
            if (ev.key === "Escape") {
                this.state.suggest = null;
                return;
            }
        }
        if (ev.key === "Escape") {
            this.state.newRow = blankRow(this.defaultDate());
            this.state.suggest = null;
            return;
        }
        if (ev.key !== "Enter") {
            return;
        }
        ev.preventDefault();
        const row = this.state.newRow;
        if (field === "date") {
            this.onNewDate(ev);
        }
        if (field === "item" && !row.product) {
            this.notification.add(row.query ? _t("No item matches “%s”. Try part of the name or the code.", row.query)
                : _t("Type the item: a code or a name."), { type: "warning" });
            return;
        }
        if (ev.ctrlKey || ev.metaKey || field === "partner_name") {
            this.saveNew();
            return;
        }
        let next = NEW_COLS[NEW_COLS.indexOf(field) + 1];
        if (field === "qty_in" && parseAmount(row.qty_in)) {
            next = "reason";
        }
        if (field === "reason" && !WITH_PARTNER.has(row.reason)) {
            this.saveNew();
            return;
        }
        if (next) {
            this.focusNew(next);
        }
    }

    onNewDate(ev) {
        const value = parseDay(ev.target.value, this.state.year, this.state.month);
        if (value) {
            this.state.newRow.date = value;
        } else {
            this.notification.add(_t("Type a day like 3, 3/11 or 3 Nov."), { type: "warning" });
        }
        ev.target.value = this.shortDate(this.state.newRow.date);
    }

    onNewBlur(field) {
        setTimeout(() => {
            if (this.state.suggest?.field === field) {
                this.state.suggest = null;
            }
        }, 150);
    }

    newProblem() {
        const row = this.state.newRow;
        if (!row.product) {
            return ["item", row.query ? _t("Pick the item from the list.") : _t("Type the item: a code or a name.")];
        }
        const qtyIn = parseAmount(row.qty_in, NaN);
        const qtyOut = parseAmount(row.qty_out, NaN);
        if ((row.qty_in && Number.isNaN(qtyIn)) || (row.qty_out && Number.isNaN(qtyOut))) {
            return [Number.isNaN(qtyIn) ? "qty_in" : "qty_out", _t("Type a quantity, e.g. 240.")];
        }
        if (!(qtyIn > 0) && !(qtyOut > 0)) {
            return ["qty_in", _t("Type how many in Added or Removed.")];
        }
        if (!row.reason) {
            return ["reason", _t("Pick a reason.")];
        }
        return null;
    }

    get newReady() {
        const row = this.state.newRow;
        return Boolean(row && row.product && (row.qty_in || row.qty_out) && row.reason);
    }

    async saveNew() {
        if (this.state.saving) {
            return;
        }
        const problem = this.newProblem();
        if (problem) {
            this.notification.add(problem[1], { type: "warning" });
            this.focusNew(problem[0]);
            return;
        }
        const row = this.state.newRow;
        this.state.saving = true;
        try {
            const saved = await this.mutate(() => this.orm.call("odin.stock.book", "book_save", [false, {
                date: row.date, product_id: row.product.id, reason: row.reason,
                qty_in: parseAmount(row.qty_in), qty_out: parseAmount(row.qty_out),
                partner_name: row.partner_name || false, warehouse_id: this.state.warehouseId,
            }]), {
                flash: (result) => [result.id], reveal: true,
                onResult: (result) => result.components.length && (this.state.open[result.id] = true),
            });
            this.state.newRow = blankRow(row.date);
            this.state.suggest = null;
            this.tellMoved(saved);
            if (saved.reason === "received" && ["need_cost", "need_supplier"].includes(saved.status_key)) {
                // Goods bought: finish it as a purchase, with this line in it.
                if (saved.date.startsWith(this.monthPrefix)) {
                    this.state.selectedId = saved.id;
                }
                this.openPurchaseFor(saved);
                return;
            }
            if (saved.components.length) {
                this.notification.add(few(saved.components.length, _t("1 component from the recipe: check it, then post."),
                    _t("%s components from the recipe: check them, then post.", saved.components.length)), { type: "info" });
            } else if (saved.status_key !== "ready" && !saved.moved_to) {
                this.notification.add(`${saved.product.name}: ${saved.status}. ${saved.detail}`, { type: "warning" });
            }
            if (!saved.date.startsWith(this.monthPrefix)) {
                this.notification.add(_t("Saved in %s: open its tab to see it.", this.shortDate(saved.date)),
                    { type: "info" });
            }
        } finally {
            this.state.saving = false;
        }
        this.focusNew("item");
    }

    /**
     * Rows copied from Excel (more than one cell) pasted into the entry row
     * become draft lines, one per row; what cannot be read is listed.
     */
    async onNewPaste(ev) {
        const text = ev.clipboardData?.getData("text/plain") || "";
        if (!/[\t\n]/.test(text.trim())) {
            return;
        }
        ev.preventDefault();
        if (this.state.pasting) {
            return;
        }
        this.state.pasting = true;
        try {
            const result = await this.mutate(() => this.orm.call("odin.stock.book", "book_paste", [text], {
                year: this.state.year, month: this.state.month, warehouse_id: this.state.warehouseId,
            }), { flash: (r) => r.ids });
            const skipped = result.skipped.map((s) => _t("Row %(row)s: %(why)s", s));
            const lines = [few(result.created, _t("1 line added as a draft."), _t("%s lines added as drafts.", result.created)), ...result.notes, ...skipped];
            const options = {
                title: skipped.length ? few(skipped.length, _t("Pasted, 1 row left out"), _t("Pasted, %s rows left out", skipped.length)) : _t("Pasted"),
                type: skipped.length || result.notes.length ? "warning" : "success",
                sticky: Boolean(skipped.length || result.notes.length), className: "o_sb_notice",
            };
            const message = lines.slice(0, 12).join("\n") + (lines.length > 12 ? "\n…" : "");
            if (result.ids.length) {
                this.notifyUndo(message, () => this.discard(result.ids), options);
            } else {
                this.notification.add(message, options);
            }
            const elsewhere = (result.months || []).filter(([y, m]) => y !== this.state.year || m !== this.state.month);
            if (elsewhere.length) {
                this.notification.add(_t("Some rows are dated in another month: open its tab to see them."),
                    { type: "info" });
            }
            this.state.newRow = blankRow(this.defaultDate());
        } finally {
            this.state.pasting = false;
        }
    }

    focusNew(field) {
        const go = () => {
            const el = this.root.el?.querySelector(`.o_sb_new [data-field="${field}"]`);
            if (el) {
                el.focus({ preventScroll: true });
                el.select?.();
            }
            return Boolean(el);
        };
        if (!go()) {
            setTimeout(go);
        }
    }

    onGlobalKey(ev) {
        // A window above the page has the keyboard.
        if (!this.root.el?.isConnected || document.querySelector(".o_dialog")) {
            return;
        }
        const typing = ev.target.closest?.("input, textarea, select");
        if (ev.key === "Escape" && !typing) {
            this.state.selectedId = null;
            this.state.confirmCount = false;
        } else if (ev.key === "/" && !typing) {
            ev.preventDefault();
            this.root.el.querySelector(".o_sb_search input")?.focus();
        } else if (ev.key === "p" && !typing && this.state.view === "log" && !ev.ctrlKey && !ev.metaKey
                   && this.state.book && !this.state.book.empty) {
            ev.preventDefault();
            this.openPurchase();
        } else if (ev.key === "n" && !typing && this.state.view === "log" && !ev.ctrlKey && !ev.metaKey) {
            ev.preventDefault();
            this.startNew();
        }
    }

    // ------------------------------------------------------------ actions
    async post(ids, allowShort = false) {
        let count = 0;
        const refs = [];
        const skipped = [];
        const failed = [];
        for (let at = 0; at < ids.length; at += POST_BATCH) {
            const batch = ids.slice(at, at + POST_BATCH);
            try {
                const result = await this.mutate(() => this.orm.call("odin.stock.book", "book_post", [batch],
                    { allow_short: allowShort }), { flash: batch });
                count += result.count;
                refs.push(...result.refs);
                skipped.push(...result.skipped);
                failed.push(...result.failed);
            } catch (error) {
                // What went before is saved: say so, then show the error.
                if (count) {
                    this.notification.add(_t("%(n)s posted before this stopped. The rest are still drafts.", { n: count }),
                        { type: "warning", sticky: true });
                }
                throw error;
            }
        }
        const shown = refs.length > 3 ? `${refs[0]} … ${refs[refs.length - 1]}` : refs.join(", ");
        if (count) {
            this.notification.add(_t("%(n)s posted: %(refs)s", { n: count, refs: shown }), { type: "success" });
        }
        // Whatever stayed a draft is named, with why: never a silent shortfall.
        const stayed = [...failed, ...skipped];
        if (stayed.length) {
            const lines = stayed.slice(0, 10).map((s) => `${s.item}: ${s.why}`);
            this.notification.add(lines.join("\n") + (stayed.length > 10 ? "\n…" : ""), {
                title: few(stayed.length, _t("1 line was not posted"), _t("%s lines were not posted", stayed.length)), type: failed.length ? "danger" : "warning",
                sticky: true, className: "o_sb_notice",
            });
        } else if (!count) {
            this.notification.add(_t("Nothing was ready to post."), { type: "warning" });
        }
    }

    /** What posting the ready lines will create, so a supervisor posts on purpose. */
    postAll() {
        const ids = this.readyIds;
        const rows = this.rows.filter((r) => ids.includes(r.id));
        const byKind = {};
        for (const r of rows) {
            byKind[r.posts_as] = (byKind[r.posts_as] || 0) + 1;
        }
        const body = [
            few(ids.length, _t("Posting 1 line creates:"), _t("Posting %s lines creates:", ids.length)),
            ...Object.entries(byKind).map(([kind, n]) => `• ${n} × ${kind}`),
            _t("Lines post in date order. Any that cannot post stay as drafts and are listed; the others still post."),
            _t("Posted lines are locked. A mistake is fixed with a new line."),
        ].join("\n");
        this.dialog.add(ConfirmationDialog, {
            title: few(ids.length, _t("Post 1 line?"), _t("Post %s lines?", ids.length)),
            body,
            confirmLabel: _t("Post %s", ids.length),
            confirm: () => this.post(ids),
            cancel: () => {},
        });
    }

    /** Undo of Copy, Reverse and Paste: the drafts they added go. */
    async discard(ids) {
        const { removed, kept } = await this.mutate(() => this.orm.call("odin.stock.book", "book_discard", [ids]));
        if (kept) {
            this.notification.add(_t("%(removed)s removed. %(kept)s posted in the meantime and stay.", { removed, kept }),
                { type: "warning" });
        }
    }

    async copyLine(row) {
        const id = await this.mutate(() => this.orm.call("odin.stock.book", "book_copy", [row.id],
            { day: this.defaultDate() }), { flash: (newId) => [newId], reveal: true });
        this.notifyUndo(_t("Copied as a new draft."), () => this.discard([id]));
        this.state.selectedId = this.rows.some((r) => r.id === id) ? id : null;
    }

    reverseLine(row) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Reverse %s?", row.ref),
            body: _t("A draft %(reason)s line for the same item and quantity is added today, with a note that it reverses %(ref)s. Post it to undo the movement.",
                { reason: row.reason === "sale" ? _t("Customer return") : row.reason === "return" ? _t("Sale") : _t("Correction"), ref: row.ref }),
            confirmLabel: _t("Add the reversing line"),
            confirm: async () => {
                const id = await this.mutate(() => this.orm.call("odin.stock.book", "book_reverse", [row.id]),
                    { flash: (newId) => [newId], reveal: true });
                const today = this.state.book.today;
                this.notifyUndo(today.startsWith(this.monthPrefix)
                    ? _t("Reversing line added as a draft.")
                    : _t("Reversing line added as a draft, in today's month."), () => this.discard([id]));
            },
            cancel: () => {},
        });
    }

    /** A posted line and the line that reverses it, off the book together. */
    removePair(row) {
        const other = row.reverses || row.reversed_by;
        this.dialog.add(ConfirmationDialog, {
            title: _t("Remove %(ref)s and %(other)s?", { ref: row.ref, other }),
            body: _t("The two lines cancel each other out, so both come off the Stock Book. Their stock moves and journal entries stay as they are, and stock on hand does not change."),
            confirmLabel: _t("Remove both"),
            confirm: async () => {
                this.state.selectedId = null;
                const ids = await this.mutate(() => this.orm.call("odin.stock.book", "book_remove_pair", [row.id]));
                this.notifyUndo(_t("%(ref)s and %(other)s removed.", { ref: row.ref, other }), () => this.mutate(
                    () => this.orm.call("odin.stock.book", "book_restore_pair", [ids]), { flash: ids, reveal: true }));
            },
            cancel: () => {},
        });
    }

    /** A short line: go and count the item that is short. */
    countItem(product) {
        return this.navigate(() => {
            this.state.view = "count";
            this.state.selectedId = null;
            this.state.group = "";
            this.state.search = product.code || product.name;
        });
    }

    async cancel(row) {
        this.state.selectedId = null;
        await this.mutate(() => this.orm.call("odin.stock.book", "book_cancel", [[row.id]]));
        const name = row.product ? row.product.name : _t("Line");
        this.notifyUndo(_t("%s: line cancelled.", name), () => this.mutate(
            () => this.orm.call("odin.stock.book", "book_restore", [[row.id]]), { flash: [row.id], reveal: true }));
    }

    /**
     * A whole LPO or invoice in one window; its lines land on the book.
     * ``lineId``: starting from a draft line, which saving replaces.
     * ``items``: starting with these items, from a shortage.
     */
    openPurchase({ lineId = false, items = [], kind = "purchase", loaded = undefined } = {}) {
        this.dialog.add(PurchaseDialog, {
            warehouseId: this.state.warehouseId || this.state.book?.warehouse?.id || false,
            lineId, items, kind, loaded,
            onSaved: (result) => this.purchaseSaved(result),
        });
    }

    openPurchaseFor(row) {
        this.openPurchase({ lineId: row.id, kind: row.reason === "sale" ? "sale" : "purchase" });
    }

    /** A saved purchase taken back in one step, then open again to correct. */
    undoPurchase(row) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Undo %s?", row.purchase_ref || row.purchase),
            body: _t("The payment and bill are cancelled, the goods leave stock again, and the order is cancelled. The documents stay, marked cancelled. It then opens again here, for you to correct or close."),
            confirmLabel: _t("Undo purchase"),
            confirm: async () => {
                const result = await this.mutate(() => this.orm.call("odin.stock.book", "book_purchase_undo", [row.id]));
                this.notification.add(_t("%(order)s (%(ref)s) undone.", { order: result.order, ref: result.purchase_ref }),
                    { type: "success" });
                this.openPurchase({ loaded: result.again });
            },
            cancel: () => {},
        });
    }

    // ------------------------------------------------------------ what each status asks for
    /** Draft lines whose status says what to do next are buttons. */
    pillActs(row) {
        return row.state === "draft" && !["ready", "cancelled", "posted"].includes(row.status_key);
    }

    onPill(row) {
        if ((row.reason === "received" && ["need_cost", "need_supplier"].includes(row.status_key))
                || row.status_key === "need_invoice") {
            this.openPurchaseFor(row);
            return;
        }
        this.state.selectedId = row.id;
        const cell = {
            need_reason: "reason", need_partner: "partner", future: "date",
            need_qty: row.direction === "out" ? "qty_out" : "qty_in",
            wrong_way: row.direction === "out" ? "qty_out" : "qty_in",
        }[row.status_key];
        if (cell) {
            this.later(() => {
                const el = this.root.el?.querySelector(`.o_sb_row[data-id="${row.id}"] [data-cell="${cell}"]`);
                el?.focus();
                el?.select?.();
            }, 30);
        }
    }

    // ------------------------------------------------------------ the banner a card opens
    /** What is short across the lines shown, most short first. */
    get shortItems() {
        const items = new Map();
        const add = (product, onhand, uom) => {
            if (onhand !== null && onhand !== undefined && onhand < 0) {
                const seen = items.get(product.id);
                if (!seen || -onhand > seen.short) {
                    items.set(product.id, { product: { ...product, stock: true }, short: -onhand, uom });
                }
            }
        };
        for (const row of this.rows) {
            if (row.status_key === "short") {
                if (row.qty_out) {
                    add(row.product, row.onhand_after, row.uom);
                }
                row.components.filter((c) => !c.unlimited).forEach((c) => add(c.product, c.onhand_after, c.uom));
            }
        }
        return [...items.values()].sort((a, b) => b.short - a.short);
    }

    get focus() {
        const n = this.rows.length;
        const month = this.monthLabel;
        const texts = {
            need: n ? {
                title: few(n, _t("1 line in %s is missing something", month), _t("%(n)s lines in %(month)s are missing something", { n, month })),
                text: _t("Each line's status says what. Click the status to fill it in."),
            } : { title: _t("Nothing is missing in %s", month), text: _t("Every line has what it needs.") },
            short: n ? {
                title: few(n, _t("1 line in %s uses more than is on hand", month), _t("%(n)s lines in %(month)s use more than is on hand", { n, month })),
                text: _t("Buy or make what is missing, or count it, and the lines become ready. If the stock really is there, open a line and post it anyway."),
            } : { title: _t("Nothing is short in %s", month), text: _t("Every line has enough on hand.") },
            ready: n ? {
                title: few(n, _t("1 line in %s is ready to post", month), _t("%(n)s lines in %(month)s are ready to post", { n, month })),
                text: this.state.book.can_post ? _t("Posting creates the real documents and locks each line.")
                    : _t("A supervisor posts them."),
            } : { title: _t("Nothing is waiting to post in %s", month), text: "" },
        };
        return texts[this.state.status] || { title: "", text: "" };
    }

    // ------------------------------------------------------------ out for recipes
    togglePick(row) {
        this.state.picked[row.product.id] = !this.state.picked[row.product.id];
    }

    get pickedRows() {
        return (this.state.recipes?.rows || []).filter((r) => this.state.picked[r.product.id] && !r.made);
    }

    buyPicked() {
        const items = this.pickedRows.map((r) => r.product);
        this.state.picked = {};
        this.openPurchase({ items });
    }

    /** An item made here: its filling or batch, in the entry row. */
    async makeItem(row) {
        await this.setView("log");
        this.state.status = "";
        this.state.search = "";
        await this.load();
        await this.rendered();
        const line = this.state.newRow;
        line.product = row.product;
        line.query = row.product.display_name;
        line.reason = row.reason;
        this.scrollToEnd();
        this.focusNew("qty_in");
    }

    async purchaseSaved(result) {
        await this.load();
        if (this.state.view === "log") {
            this.flashRows(result.ids, true);
        }
        const buttons = result.posted ? await this.printButtons(result.ids[0]) : [];
        if (result.kind === "sale") {
            const lines = few(result.ids.length, _t("1 line"), _t("%s lines", result.ids.length));
            const paidIn = result.paid_from ? _t(", paid into %s", result.paid_from) : "";
            this.notification.add(result.posted
                ? _t("%(order)s (%(ref)s): %(lines)s delivered, invoice %(invoice)s%(paid)s", {
                    order: result.order, ref: result.purchase_ref, lines, invoice: result.invoice || _t("none, free goods"), paid: paidIn })
                : _t("%(ref)s: waiting for a supervisor to post", { ref: result.purchase_ref }),
                { type: "success", buttons, sticky: buttons.length > 0 });
            if (!result.date.startsWith(this.monthPrefix)) {
                this.notification.add(_t("Saved in %s: open its tab to see it.", this.shortDate(result.date)), { type: "info" });
            }
            return;
        }
        const money = result.total ? ` · ${this.state.book?.currency || ""} ${this.fmt(result.total, 2)}` : "";
        const paid = result.paid_from ? _t(", paid from %s", result.paid_from) : _t(", not paid yet");
        const text = result.posted
            ? _t("%(order)s (%(ref)s): %(lines)s received, bill %(bill)s%(paid)s%(money)s", {
                order: result.order, ref: result.purchase_ref, bill: result.bill, paid, money,
                lines: few(result.ids.length, _t("1 line"), _t("%s lines", result.ids.length)),
            })
            : _t("%(ref)s: waiting for a supervisor to post", { ref: result.purchase_ref });
        this.notification.add(text, { type: "success", buttons, sticky: buttons.length > 0 });
        if (!result.date.startsWith(this.monthPrefix)) {
            this.notification.add(_t("Saved in %s: open its tab to see it.", this.shortDate(result.date)), { type: "info" });
        }
    }

    /** Received but not billed: their stock is not in the accounts yet. */
    openToBill() {
        this.action.doAction({
            type: "ir.actions.act_window", name: _t("Purchases to bill"), res_model: "purchase.order",
            views: [[false, "list"], [false, "form"]], domain: [["invoice_status", "=", "to invoice"]],
        });
    }

    openBill(row) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "account.move", res_id: row.bill_id,
                               views: [[false, "form"]] });
    }

    async openDocument(row) {
        const action = await this.orm.call("odin.stock.line", "action_open_document", [[row.id]]);
        this.action.doAction(action);
    }

    openForm(row) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "odin.stock.line", res_id: row.id,
                               views: [[false, "form"]] });
    }

    async exportXlsx() {
        await download({ url: "/odin_stock_book/export", data: {
            year: this.state.year, month: this.state.month, warehouse_id: this.state.warehouseId || "",
        } });
    }

    /** The movement log as a PDF to sign: the month on the tab, or one ``day``. */
    async printSheet(day = false) {
        const action = await this.orm.call("odin.stock.book", "book_print", [], {
            year: this.state.year, month: this.state.month, day, warehouse_id: this.state.warehouseId,
        });
        await this.action.doAction(action);
    }

    // ------------------------------------------------------------ count
    setCount(row, ev) {
        const raw = ev.target.value.trim();
        const qty = raw === "" ? null : parseAmount(raw, NaN);
        if (qty !== null && (Number.isNaN(qty) || qty < 0)) {
            this.notification.add(_t("Type what is on the shelf, e.g. 412."), { type: "warning" });
            ev.target.value = row.counted === null ? "" : this.fmt(row.counted);
            return;
        }
        return this.mutate(() => this.orm.call("odin.stock.book", "count_set", [row.product.id, qty],
            { warehouse_id: this.state.warehouseId }));
    }

    onCountKey(ev) {
        if (ev.key === "Enter" || ev.key === "ArrowDown" || ev.key === "ArrowUp") {
            ev.preventDefault();
            const inputs = [...this.root.el.querySelectorAll(".o_sb_count_input")];
            const at = inputs.indexOf(ev.target);
            const next = inputs[at + (ev.key === "ArrowUp" ? -1 : 1)];
            ev.target.blur();
            if (next) {
                setTimeout(() => {
                    next.focus();
                    next.select();
                });
            }
        }
    }

    /**
     * ``resolve``: when stock moved after items were counted, "counted" sets
     * them to the count, "difference" applies what was found missing (or
     * extra) to what is there now.
     */
    async postCount(resolve = false) {
        const result = await this.mutate(() => this.orm.call("odin.stock.book", "count_post", [], {
            warehouse_id: this.state.warehouseId, resolve,
        }));
        this.state.confirmCount = false;
        if (result.moved) {
            this.state.countConflicts = result.moved;
            return;
        }
        this.state.countConflicts = null;
        this.notification.add(few(result.applied, _t("Count posted: 1 item adjusted."), _t("Count posted: %s items adjusted.", result.applied)), { type: "success" });
    }
}

registry.category("actions").add("odin_stock_book.book", StockBook);
