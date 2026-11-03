import { Component, onMounted, onWillStart, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { debounce } from "@web/core/utils/timing";
import { download } from "@web/core/network/download";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";

// Send-money tariff (KES), the same table the server uses. Shown while typing;
// the statement brings the real charge for pay bill and till payments.
const SEND_TARIFF = [[100, 0], [500, 7], [1000, 13], [1500, 23], [2500, 33], [3500, 53], [5000, 57],
    [7500, 78], [10000, 90], [15000, 100], [20000, 105], [250000, 108]];
export function sendMoneyCharge(amount, phone, method) {
    const digits = (phone || "").replace(/\D/g, "");
    if (method !== "mpesa" || digits.length < 9 || !amount) {
        return 0;
    }
    const hit = SEND_TARIFF.find(([limit]) => amount <= limit);
    return hit ? hit[1] : 108;
}

/** "1,500" or "1500.50" → number; `fallback` when it is not a number. */
export function parseAmount(value, fallback = 0) {
    if (value === "" || value === null || value === undefined) {
        return fallback === 0 ? 0 : fallback;
    }
    const n = Number(String(value).replace(/[,\s]/g, ""));
    return Number.isNaN(n) ? fallback : n;
}

const MONTH_KEYS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/**
 * A day typed the quick way, Kenyan order: "3" (this month), "3/11",
 * "3/11/26", "3 nov", or an ISO date. Returns "YYYY-MM-DD" or null.
 */
export function parseDay(text, year, month) {
    const value = String(text || "").trim().toLowerCase();
    if (!value) {
        return null;
    }
    let d, m = month, y = year, match;
    if ((match = value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) {
        [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])];
    } else if ((match = value.match(/^(\d{1,2})$/))) {
        d = Number(match[1]);
    } else if ((match = value.match(/^(\d{1,2})[/.\- ](\d{1,2})(?:[/.\- ](\d{2,4}))?$/))) {
        d = Number(match[1]);
        m = Number(match[2]);
        y = match[3] ? Number(match[3].length === 2 ? "20" + match[3] : match[3]) : year;
    } else if ((match = value.match(/^(\d{1,2})\s*([a-z]{3})[a-z]*\.?(?:\s+(\d{2,4}))?$/))) {
        d = Number(match[1]);
        m = MONTH_KEYS.indexOf(match[2]) + 1;
        y = match[3] ? Number(match[3].length === 2 ? "20" + match[3] : match[3]) : year;
    } else {
        return null;
    }
    const date = new Date(Date.UTC(y, m - 1, d));
    if (!m || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d) {
        return null;
    }
    return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

// What the status pill says in the grid; the panel shows the full sentence.
const SHORT_STATUS = {
    need_amount: "Amount?", need_description: "What for?", need_category: "Category?",
    need_stock: "Stock details", need_receipt: "Receipt?", ready: "Ready", posted: "Approved",
    cancelled: "Cancelled",
};

const MPESA_CODE = /^[A-Za-z0-9]{10}$/;

// Columns a person types into, in the sheet's order. Enter and Tab move along
// this list; the last one saves the row.
const EDIT_COLS = ["date", "description", "paid_to", "phone", "method", "mpesa_code", "amount"];
const NEED = ["need_amount", "need_description", "need_category", "need_receipt"];

function blankRow(today, keep = {}) {
    return { date: keep.date || today, description: "", paid_to: "", phone: "", method: keep.method || "mpesa",
             mpesa_code: "", amount: "", category: "", needs_stock: false };
}

export class PettyCashBook extends Component {
    static template = "odin_petty_cash.Book";
    static components = { Layout, Dropdown, DropdownItem };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.root = useRef("root");
        const params = this.props.action.params || {};
        this.state = useState({
            loading: true, book: null, floatId: params.float_id || false, year: false, month: false,
            filter: "", search: "", selectedId: null, newRow: null, suggest: null, saving: false,
            topup: null, product: { query: "", results: [] }, receipts: [], dragging: false,
        });
        this.previewRule = debounce(this.previewRule.bind(this), 250);
        this.suggestFor = debounce(this.suggestFor.bind(this), 200);
        this.searchProducts = debounce(this.searchProducts.bind(this), 200);
        this.reloadSoon = debounce(() => this.load(), 150);
        onWillStart(() => this.load());
        onMounted(() => this.focusNew("description"));
        useExternalListener(window, "keydown", (ev) => this.onGlobalKey(ev));
    }

    // ------------------------------------------------------------ data
    async load() {
        const book = await this.orm.call("odin.petty.cash.float", "get_book", [], {
            float_id: this.state.floatId, year: this.state.year, month: this.state.month,
            status: this.state.filter, search: this.state.search,
        });
        this.state.book = book;
        if (!book.empty) {
            this.state.floatId = book.float.id;
            this.state.year = book.year;
            this.state.month = book.month;
            if (!this.state.newRow) {
                this.state.newRow = blankRow(this.defaultDate());
            }
        }
        this.state.loading = false;
        if (this.state.selectedId && !this.selected) {
            this.state.selectedId = null;
        }
    }

    defaultDate() {
        const book = this.state.book;
        const today = book.today;
        const [y, m] = today.split("-").map(Number);
        if (y === book.year && m === book.month) {
            return today;
        }
        return `${book.year}-${String(book.month).padStart(2, "0")}-01`;
    }

    get rows() {
        return this.state.book?.rows || [];
    }

    get selected() {
        return this.rows.find((r) => r.id === this.state.selectedId);
    }

    get monthStart() {
        return `${this.state.book.year}-${String(this.state.book.month).padStart(2, "0")}-01`;
    }

    get monthLabel() {
        const m = this.state.book.months[this.state.book.month - 1];
        return `${m.label} ${this.state.book.year}`;
    }

    get statementDiff() {
        const f = this.state.book.float;
        if (f.statement_balance === null || f.statement_balance === undefined) {
            return null;
        }
        return Math.round((f.book_balance - f.statement_balance) * 100) / 100;
    }

    fmt(value, blankZero = false) {
        if (value === "" || value === null || value === undefined || (blankZero && !value)) {
            return "";
        }
        return Number(value).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    shortDate(value) {
        if (!value) {
            return "";
        }
        const [y, m, d] = value.split("-").map(Number);
        return `${d} ${this.state.book.months[m - 1].label.charAt(0)}${this.state.book.months[m - 1].label.slice(1).toLowerCase()}`;
    }

    shortStatus(row) {
        return SHORT_STATUS[row.status_key] || row.status;
    }

    // ------------------------------------------------------------ export
    async exportXlsx(scope) {
        await download({
            url: "/odin_petty_cash/export",
            data: { float_id: this.state.floatId, year: this.state.book.year, month: this.state.book.month, scope },
        });
    }

    // ------------------------------------------------------------ navigation
    async setMonth(month) {
        this.state.month = month;
        this.state.selectedId = null;
        this.state.newRow = null;
        await this.load();
    }

    async openOlder() {
        const older = this.state.book.older_open;
        this.state.year = older.year;
        this.state.filter = "";
        await this.setMonth(older.month);
    }

    async shiftYear(delta) {
        this.state.year += delta;
        this.state.newRow = null;
        await this.load();
    }

    async setFilter(filter) {
        this.state.filter = this.state.filter === filter ? "" : filter;
        await this.load();
    }

    async setFloat(ev) {
        this.state.floatId = Number(ev.target.value);
        this.state.newRow = null;
        await this.load();
    }

    onSearch(ev) {
        this.state.search = ev.target.value;
        this.reloadSoon();
    }

    select(row) {
        this.state.selectedId = this.state.selectedId === row.id ? null : row.id;
        this.state.product = { query: "", results: [] };
        this.state.receipts = [];
        if (this.state.selectedId) {
            this.loadReceipts();
        }
    }

    onRowClick(ev, row) {
        if (ev.target.closest("input, select, button, .o_pc_suggest")) {
            return;
        }
        this.select(row);
    }

    // ------------------------------------------------------------ editing existing rows
    async saveCell(row, field, value) {
        if (row.state !== "draft") {
            return;
        }
        if (field === "amount" || field === "quantity") {
            value = value === "" ? 0 : parseAmount(value, NaN);
            if (Number.isNaN(value)) {
                this.notification.add(_t("Type the amount as a number, e.g. 1500"), { type: "warning" });
                return;
            }
        }
        if (String(row[field] ?? "") === String(value ?? "")) {
            return;
        }
        try {
            await this.orm.call("odin.petty.cash.line", "book_save", [row.id, { [field]: value }]);
        } catch (error) {
            this.reloadSoon();
            throw error;
        }
        await this.load();
    }

    onCellChange(ev, row, field) {
        let value = ev.target.value;
        if (field === "mpesa_code" && value && !MPESA_CODE.test(value.trim())) {
            this.notification.add(_t("M-Pesa codes are 10 letters and digits, like UK30R1F8QM"), { type: "warning" });
            ev.target.value = row.mpesa_code;
            return;
        }
        if (field === "date") {
            value = parseDay(value, this.state.book.year, this.state.book.month);
            if (!value) {
                this.notification.add(_t("Type a day like 3, 3/11 or 3 Nov"), { type: "warning" });
                ev.target.value = this.shortDate(row.date);
                return;
            }
        }
        this.saveCell(row, field, value);
    }

    toggleMethod(row) {
        this.saveCell(row, "method", row.method === "cash" ? "mpesa" : "cash");
    }

    async setRule(row, ruleId) {
        await this.orm.call("odin.petty.cash.line", "book_save", [row.id, { rule_id: Number(ruleId) || false }]);
        await this.load();
    }

    // ------------------------------------------------------------ the new row
    onNewDate(ev) {
        const value = parseDay(ev.target.value, this.state.book.year, this.state.book.month);
        if (value) {
            this.state.newRow.date = value;
        } else {
            this.notification.add(_t("Type a day like 3, 3/11 or 3 Nov"), { type: "warning" });
        }
        ev.target.value = this.shortDate(this.state.newRow.date);
    }

    setNewMethod(method) {
        const row = this.state.newRow;
        row.method = method || (row.method === "cash" ? "mpesa" : "cash");
        if (row.method === "cash") {
            row.mpesa_code = "";
        }
    }

    onNewInput(field, ev) {
        const row = this.state.newRow;
        row[field] = ev.target.value;
        if (field === "description") {
            this.previewRule();
            this.suggestFor("description", ev.target.value);
        } else if (field === "paid_to") {
            this.suggestFor("paid_to", ev.target.value);
            if (row.description) {
                this.previewRule();
            }
        }
    }

    get newAmount() {
        return parseAmount(this.state.newRow.amount);
    }

    get newCharge() {
        const row = this.state.newRow;
        return sendMoneyCharge(this.newAmount, row.phone, row.method);
    }

    async previewRule() {
        const row = this.state.newRow;
        if (!row || !row.description) {
            if (row) {
                row.category = "";
                row.needs_stock = false;
            }
            return;
        }
        const res = await this.orm.call("odin.petty.cash.line", "book_preview_rule",
            [this.state.floatId, row.description, "payment", row.paid_to]);
        row.category = res.category;
        row.needs_stock = res.needs_stock;
    }

    async suggestFor(field, text) {
        if (!text || text.length < 2) {
            this.state.suggest = null;
            return;
        }
        const method = field === "paid_to" ? "book_suggest_payee" : "book_suggest_description";
        const items = await this.orm.call("odin.petty.cash.line", method, [this.state.floatId, text]);
        this.state.suggest = items.length ? { field, items, index: 0 } : null;
    }

    pickSuggestion(item) {
        const row = this.state.newRow;
        const s = this.state.suggest;
        if (!s) {
            return;
        }
        if (s.field === "paid_to") {
            row.paid_to = item.paid_to;
            if (!row.phone && item.phone) {
                row.phone = item.phone;
            }
            if (item.method) {
                row.method = item.method;
            }
            if (!row.description && item.usual) {
                row.description = item.usual;
                this.previewRule();
            }
            this.state.suggest = null;
            this.focusNew(row.phone ? "mpesa_code" : "phone");
        } else {
            row.description = item;
            this.state.suggest = null;
            this.previewRule();
            this.focusNew("paid_to");
        }
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
        if (field === "method" && ["m", "c", " "].includes(ev.key.toLowerCase())) {
            ev.preventDefault();
            this.setNewMethod({ m: "mpesa", c: "cash" }[ev.key.toLowerCase()]);
            return;
        }
        if (ev.key === "Enter") {
            ev.preventDefault();
            if (field === "date") {
                this.onNewDate(ev);
            }
            const at = EDIT_COLS.indexOf(field);
            if (field === "amount" || (ev.ctrlKey || ev.metaKey)) {
                this.saveNew();
            } else {
                let next = EDIT_COLS[at + 1];
                if (next === "mpesa_code" && this.state.newRow.method === "cash") {
                    next = "amount";
                }
                this.focusNew(next);
            }
        } else if (ev.key === "Escape") {
            this.state.newRow = blankRow(this.defaultDate(), this.state.newRow);
            this.state.suggest = null;
        }
    }

    onNewBlur(field) {
        // Let a click on a suggestion land before the list closes.
        setTimeout(() => {
            if (this.state.suggest?.field === field) {
                this.state.suggest = null;
            }
        }, 150);
    }

    get newRowReady() {
        const row = this.state.newRow;
        return row && (row.description || row.paid_to || row.amount);
    }

    /** What stops the new row from saving, with the cell to fix. */
    newRowProblem() {
        const row = this.state.newRow;
        const amount = parseAmount(row.amount, NaN);
        if (row.amount !== "" && (Number.isNaN(amount) || amount < 0)) {
            return ["amount", _t("Paid must be a number, e.g. 1500")];
        }
        if (row.mpesa_code && !MPESA_CODE.test(row.mpesa_code.trim())) {
            return ["mpesa_code", _t("M-Pesa codes are 10 letters and digits, like UK30R1F8QM")];
        }
        if (!row.date) {
            return ["date", _t("Type a day like 3, 3/11 or 3 Nov")];
        }
        return null;
    }

    async saveNew() {
        const row = this.state.newRow;
        if (!this.newRowReady || this.state.saving) {
            return;
        }
        const problem = this.newRowProblem();
        if (problem) {
            this.notification.add(problem[1], { type: "warning" });
            this.focusNew(problem[0]);
            return;
        }
        const amount = parseAmount(row.amount);
        this.state.saving = true;
        try {
            const saved = await this.orm.call("odin.petty.cash.line", "book_save", [false, {
                float_id: this.state.floatId, date: row.date, description: row.description,
                paid_to: row.paid_to, phone: row.phone, method: row.method,
                mpesa_code: row.mpesa_code || false, amount,
            }]);
            this.state.newRow = blankRow(this.defaultDate(), row);
            this.state.suggest = null;
            await this.load();
            if (saved.duplicate_of) {
                this.notification.add(_t("%(voucher)s looks like a repeat of %(twin)s: same payee, amount and day. Cancel it if it is.",
                    { voucher: saved.voucher, twin: saved.duplicate_of }), { type: "warning", sticky: true });
            } else if (saved.status_key !== "ready") {
                this.notification.add(`${saved.voucher}: ${saved.status.toLowerCase()}`, { type: "info" });
            }
        } finally {
            this.state.saving = false;
        }
        this.focusNew("description");
    }

    focusNew(field) {
        // Focus at once when the cell exists, so the next keystroke lands in
        // it even when typing fast; otherwise after the next render.
        const go = () => {
            const el = this.root.el?.querySelector(`.o_pc_new [data-field="${field}"]`);
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
        if (!this.root.el || !this.root.el.isConnected) {
            return;
        }
        const typing = ev.target.closest?.("input, textarea, select");
        if (ev.key === "Escape" && this.state.selectedId && !typing) {
            this.state.selectedId = null;
        } else if (ev.key === "/" && !typing) {
            ev.preventDefault();
            this.root.el.querySelector(".o_pc_search input")?.focus();
        } else if (ev.key === "n" && !typing && !ev.ctrlKey && !ev.metaKey) {
            ev.preventDefault();
            this.focusNew("description");
        }
    }

    // ------------------------------------------------------------ side panel
    async loadReceipts() {
        const row = this.selected;
        if (!row) {
            return;
        }
        this.state.receipts = await this.orm.call("odin.petty.cash.line", "book_receipts", [[row.id]]);
    }

    async uploadFiles(files) {
        const row = this.selected;
        for (const file of files) {
            const datas = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(",")[1]);
                reader.onerror = reject;
                reader.readAsDataURL(file);
            });
            await this.orm.call("odin.petty.cash.line", "book_attach", [[row.id], file.name, datas]);
        }
        this.notification.add(_t("Receipt attached"), { type: "success" });
        await this.load();
        await this.loadReceipts();
    }

    onFileInput(ev) {
        this.uploadFiles([...ev.target.files]);
        ev.target.value = "";
    }

    onDrop(ev) {
        ev.preventDefault();
        this.state.dragging = false;
        if (ev.dataTransfer?.files?.length) {
            this.uploadFiles([...ev.dataTransfer.files]);
        }
    }

    onProductInput(ev) {
        this.state.product.query = ev.target.value;
        this.searchProducts();
    }

    async searchProducts() {
        const q = this.state.product.query;
        if (!q) {
            this.state.product.results = [];
            return;
        }
        this.state.product.results = await this.orm.call("product.product", "name_search", [], {
            name: q, domain: [["is_storable", "=", true]], limit: 8,
        });
    }

    async pickProduct(row, product) {
        this.state.product = { query: "", results: [] };
        await this.orm.call("odin.petty.cash.line", "book_save", [row.id, { product_id: product[0] }]);
        await this.load();
    }

    async approve(ids) {
        const count = await this.orm.call("odin.petty.cash.line", "book_approve", [ids]);
        this.notification.add(count ? _t("%s approved and posted", count) : _t("Nothing was ready to approve"),
            { type: count ? "success" : "warning" });
        await this.load();
    }

    approveAll() {
        return this.approve(this.rows.filter((r) => r.status_key === "ready").map((r) => r.id));
    }

    async reset(row) {
        await this.orm.call("odin.petty.cash.line", "action_reset", [[row.id]]);
        await this.load();
    }

    async cancel(row) {
        await this.orm.call("odin.petty.cash.line", "action_cancel", [[row.id]]);
        this.state.selectedId = null;
        await this.load();
    }

    openRecord(model, id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id,
                               views: [[false, "form"]] });
    }

    uploadStatement() {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "odin.petty.cash.import", views: [[false, "form"]],
            target: "new", name: _t("Upload M-Pesa statement"),
            context: { default_float_id: this.state.floatId },
        }, { onClose: () => this.load() });
    }

    // ------------------------------------------------------------ float top-up
    openTopup() {
        this.state.topup = { amount: "", code: "", note: "", date: this.defaultDate() };
        setTimeout(() => this.root.el?.querySelector(".o_pc_modal input")?.focus());
    }

    async saveTopup() {
        const t = this.state.topup;
        const amount = parseAmount(t.amount);
        if (!amount) {
            this.notification.add(_t("Enter the amount sent to the float."), { type: "warning" });
            return;
        }
        await this.orm.call("odin.petty.cash.float", "action_add_float",
            [this.state.floatId, amount, t.note || false, t.code || false, t.date]);
        this.state.topup = null;
        this.notification.add(_t("Float topped up"), { type: "success" });
        await this.load();
    }

    configure() {
        this.action.doAction("odin_petty_cash.odin_petty_cash_float_action");
    }
}

registry.category("actions").add("odin_petty_cash.book", PettyCashBook);
