import { Component, onMounted, onWillStart, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { debounce } from "@web/core/utils/timing";
import { download } from "@web/core/network/download";
import { parseAmount, parseDay } from "./utils";

const NEED = new Set(["need_product", "need_reason", "need_qty", "wrong_way", "need_recipe", "need_partner"]);
const REASON_COLOR = {
    filling: "#139A4A", production: "#7A5AF8", received: "#F79009", return: "#0BA5EC", sale: "#2E90FA",
    spoiled: "#F04438", sample: "#EE46BC", used: "#98A2B3", correction: "#667085",
};
const WITH_PARTNER = new Set(["sale", "return", "received"]);
// The order Enter moves through the entry row.
const NEW_COLS = ["date", "item", "qty_in", "qty_out", "reason", "partner_name"];

function blankRow(date, keep = {}) {
    return { date, product: null, query: "", qty_in: "", qty_out: "", reason: keep.reason || "", partner_name: "" };
}

export class StockBook extends Component {
    static template = "odin_stock_book.Book";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.root = useRef("root");
        const params = this.props.action.params || {};
        this.state = useState({
            view: params.view || "log", loading: true, book: null, sheet: null, count: null,
            year: false, month: false, reason: "", status: "", search: "", group: "",
            selectedId: null, open: {}, newRow: null, suggest: null, saving: false,
            compSearch: { query: "", results: [] }, confirmCount: false,
        });
        this.suggestItems = debounce(this.suggestItems.bind(this), 180);
        this.suggestPartners = debounce(this.suggestPartners.bind(this), 180);
        this.searchComponents = debounce(this.searchComponents.bind(this), 180);
        this.reloadSoon = debounce(() => this.load(), 200);
        onWillStart(() => this.load());
        onMounted(() => this.state.view === "log" && this.focusNew("item"));
        useExternalListener(window, "keydown", (ev) => this.onGlobalKey(ev));
    }

    // ------------------------------------------------------------ data
    async load() {
        const { year, month, search } = this.state;
        if (this.state.view === "log") {
            const book = await this.orm.call("odin.stock.book", "get_book", [], {
                year, month, reason: this.state.reason, status: this.state.status, search,
            });
            this.state.book = book;
            if (!book.empty) {
                this.state.year = book.year;
                this.state.month = book.month;
                if (!this.state.newRow) {
                    this.state.newRow = blankRow(this.defaultDate());
                }
            }
        } else if (this.state.view === "sheet") {
            this.state.sheet = await this.orm.call("odin.stock.book", "get_sheet", [], {
                year, month, group: this.state.group, search,
            });
            this.state.year = this.state.sheet.year;
            this.state.month = this.state.sheet.month;
        } else {
            this.state.count = await this.orm.call("odin.stock.book", "get_count", [], {
                group: this.state.group, search,
            });
        }
        this.state.loading = false;
        if (this.state.selectedId && !this.selected) {
            this.state.selectedId = null;
        }
    }

    get rows() {
        return this.state.book?.rows || [];
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
        return months ? `${months[this.state.month - 1].label} ${this.state.year}` : "";
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

    reasonColor(key) {
        return REASON_COLOR[key] || "#98A2B3";
    }

    shortRef(ref) {
        if (!ref) {
            return "";
        }
        const parts = ref.split("/");
        return `${parts[0]}/${parts[parts.length - 1]}`;
    }

    // ------------------------------------------------------------ navigation
    async setView(view) {
        this.state.view = view;
        this.state.selectedId = null;
        this.state.group = "";
        this.state.search = "";
        this.state.loading = true;
        await this.load();
        if (view === "log") {
            this.focusNew("item");
        }
    }

    async setMonth(month) {
        this.state.month = month;
        this.state.selectedId = null;
        this.state.newRow = null;
        await this.load();
    }

    async shiftYear(delta) {
        this.state.year += delta;
        this.state.newRow = null;
        await this.load();
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

    async openOlder() {
        const older = this.state.book.older_open;
        this.state.year = older.year;
        await this.setMonth(older.month);
    }

    select(row) {
        this.state.selectedId = this.state.selectedId === row.id ? null : row.id;
        this.state.compSearch = { query: "", results: [] };
    }

    toggle(row) {
        this.state.open[row.id] = !this.isOpen(row);
    }

    isOpen(row) {
        const flag = this.state.open[row.id];
        // Open by default only when a component is short, so a batch with a
        // dozen ingredients does not push the rest of the day off the screen.
        return flag === undefined
            ? row.state === "draft" && row.components.some((c) => !c.unlimited && c.onhand_after !== null && c.onhand_after < 0)
            : flag;
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
        await this.orm.call("odin.stock.book", "book_save", [row.id, values]);
        await this.load();
    }

    onCell(ev, row, field) {
        let value = ev.target.value;
        if (field === "qty_in" || field === "qty_out") {
            value = parseAmount(value, NaN);
            if (Number.isNaN(value) || value < 0) {
                this.notification.add(_t("Type a quantity, e.g. 240"), { type: "warning" });
                ev.target.value = row[field] || "";
                return;
            }
        } else if (field === "date") {
            value = parseDay(value, this.state.year, this.state.month);
            if (!value) {
                this.notification.add(_t("Type a day like 3, 3/11 or 3 Nov"), { type: "warning" });
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
            this.notification.add(_t("Type the cost per unit, e.g. 19"), { type: "warning" });
            return;
        }
        this.save(row, { unit_cost: cost });
    }

    async setComponent(row, comp, ev) {
        const qty = parseAmount(ev.target.value, NaN);
        if (Number.isNaN(qty) || qty < 0) {
            this.notification.add(_t("Type a quantity, e.g. 240"), { type: "warning" });
            ev.target.value = comp.qty;
            return;
        }
        await this.orm.call("odin.stock.book", "book_component", [row.id], { component_id: comp.id, qty });
        await this.load();
    }

    async removeComponent(row, comp) {
        await this.orm.call("odin.stock.book", "book_component", [row.id], { component_id: comp.id, remove: true });
        await this.load();
    }

    async resetComponents(row) {
        await this.orm.call("odin.stock.book", "book_reset_components", [row.id]);
        this.notification.add(_t("Components back to the recipe"), { type: "info" });
        await this.load();
    }

    onCompSearch(ev) {
        this.state.compSearch.query = ev.target.value;
        this.searchComponents();
    }

    async searchComponents() {
        const q = this.state.compSearch.query;
        this.state.compSearch.results = q ? await this.orm.call("odin.stock.book", "book_suggest_products", [q]) : [];
    }

    async addComponent(row, product) {
        this.state.compSearch = { query: "", results: [] };
        await this.orm.call("odin.stock.book", "book_component", [row.id], { product_id: product.id, qty: 1 });
        this.state.open[row.id] = true;
        await this.load();
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
        const items = await this.orm.call("odin.stock.book", "book_suggest_products", [text]);
        this.state.suggest = items.length ? { field: "item", items, index: 0 } : null;
    }

    async suggestPartners(text) {
        if (!text || text.length < 2) {
            this.state.suggest = null;
            return;
        }
        const items = await this.orm.call("odin.stock.book", "book_suggest_partners", [text]);
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
                : _t("Type the item: a code or a name"), { type: "warning" });
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
            this.notification.add(_t("Type a day like 3, 3/11 or 3 Nov"), { type: "warning" });
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
            return ["item", row.query ? _t("Pick the item from the list") : _t("Type the item: a code or a name")];
        }
        const qtyIn = parseAmount(row.qty_in, NaN);
        const qtyOut = parseAmount(row.qty_out, NaN);
        if ((row.qty_in && Number.isNaN(qtyIn)) || (row.qty_out && Number.isNaN(qtyOut))) {
            return [Number.isNaN(qtyIn) ? "qty_in" : "qty_out", _t("Type a quantity, e.g. 240")];
        }
        if (!(qtyIn > 0) && !(qtyOut > 0)) {
            return ["qty_in", _t("Type how many in Added or Removed")];
        }
        if (!row.reason) {
            return ["reason", _t("Pick a reason")];
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
            const saved = await this.orm.call("odin.stock.book", "book_save", [false, {
                date: row.date, product_id: row.product.id, reason: row.reason,
                qty_in: parseAmount(row.qty_in), qty_out: parseAmount(row.qty_out),
                partner_name: row.partner_name || false,
            }]);
            this.state.newRow = blankRow(row.date);
            this.state.suggest = null;
            if (saved.components.length) {
                this.state.open[saved.id] = true;
                this.notification.add(_t("%(n)s components from the recipe: check them, then post.",
                    { n: saved.components.length }), { type: "info" });
            } else if (saved.status_key !== "ready") {
                this.notification.add(`${saved.product.name}: ${saved.status}. ${saved.detail}`, { type: "warning" });
            }
            await this.load();
        } finally {
            this.state.saving = false;
        }
        this.focusNew("item");
    }

    focusNew(field) {
        const go = () => {
            const el = this.root.el?.querySelector(`.o_sb_new [data-field="${field}"]`);
            if (el) {
                el.focus();
                el.select?.();
            }
            return Boolean(el);
        };
        if (!go()) {
            setTimeout(go);
        }
    }

    onGlobalKey(ev) {
        if (!this.root.el?.isConnected) {
            return;
        }
        const typing = ev.target.closest?.("input, textarea, select");
        if (ev.key === "Escape" && !typing) {
            this.state.selectedId = null;
            this.state.confirmCount = false;
        } else if (ev.key === "/" && !typing) {
            ev.preventDefault();
            this.root.el.querySelector(".o_sb_search input")?.focus();
        } else if (ev.key === "n" && !typing && this.state.view === "log" && !ev.ctrlKey && !ev.metaKey) {
            ev.preventDefault();
            this.focusNew("item");
        }
    }

    // ------------------------------------------------------------ actions
    async post(ids, allowShort = false) {
        const count = await this.orm.call("odin.stock.book", "book_post", [ids], { allow_short: allowShort });
        this.notification.add(count ? _t("%s posted", count) : _t("Nothing was ready to post"),
            { type: count ? "success" : "warning" });
        await this.load();
    }

    postAll() {
        return this.post(this.readyIds);
    }

    async cancel(row) {
        await this.orm.call("odin.stock.book", "book_cancel", [[row.id]]);
        this.state.selectedId = null;
        await this.load();
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
        await download({ url: "/odin_stock_book/export", data: { year: this.state.year, month: this.state.month } });
    }

    // ------------------------------------------------------------ count
    async setCount(row, ev) {
        const raw = ev.target.value.trim();
        const qty = raw === "" ? null : parseAmount(raw, NaN);
        if (qty !== null && (Number.isNaN(qty) || qty < 0)) {
            this.notification.add(_t("Type what is on the shelf, e.g. 412"), { type: "warning" });
            ev.target.value = row.counted ?? "";
            return;
        }
        await this.orm.call("odin.stock.book", "count_set", [row.product.id, qty]);
        await this.load();
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

    async postCount() {
        const n = await this.orm.call("odin.stock.book", "count_post", []);
        this.state.confirmCount = false;
        this.notification.add(_t("Count posted: %s items adjusted", n), { type: "success" });
        await this.load();
    }
}

registry.category("actions").add("odin_stock_book.book", StockBook);
