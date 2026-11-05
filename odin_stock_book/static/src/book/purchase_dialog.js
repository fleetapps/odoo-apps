import { Component, onMounted, onWillStart, onWillUnmount, useExternalListener, useRef, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { parseAmount } from "./utils";

/** Asks first when closing would lose what was typed (the X and Escape). */
class GuardedDialog extends Dialog {
    static props = { ...Dialog.props, beforeDismiss: Function };

    async dismiss() {
        if (await this.props.beforeDismiss()) {
            return super.dismiss();
        }
    }
}

let lineKey = 0;
const blankLine = () => ({
    key: ++lineKey, product: null, query: "", note: "", qty: "", cost: "", taxId: false, taxTouched: false,
    lastCost: 0, amount: null,
});
const isBlank = (line) => !line.product && !line.query && !line.qty && !line.cost;

/**
 * One LPO or invoice, entered once: supplier, number, date, items. What Odoo's
 * purchase order asks for, on one screen; saving makes the lines the book
 * would post, under that number.
 */
export class PurchaseDialog extends Component {
    static template = "odin_stock_book.PurchaseDialog";
    static components = { Dialog: GuardedDialog };
    static props = {
        warehouseId: { type: [Number, Boolean], optional: true },
        // A draft line to start from, which saving replaces.
        lineId: { type: [Number, Boolean], optional: true },
        // Items to start with, from a shortage.
        items: { type: Array, optional: true },
        onSaved: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.dialogService = useService("dialog");
        this.root = useRef("root");
        this.state = useState({
            options: { taxes: [], journals: [], today: "", can_post: false, currency: "" },
            supplier: null, supplierQuery: "", ref: "", date: "", refProblem: "", paid: "",
            lines: [blankLine()], suggest: null, totals: null, saving: false, error: "", tried: false,
            edit: false, replaceIds: [],
        });
        this.totalsSeq = 0;
        this.refSeq = 0;
        this.timers = new Set();
        onWillStart(async () => {
            this.state.options = await this.orm.call("odin.stock.book", "book_purchase_options", []);
            this.state.date = this.state.options.today;
            await this.prefill();
            this.snapshot = this.fingerprint;
        });
        onMounted(() => this.focus(this.firstCell));
        onWillUnmount(() => this.timers.forEach((t) => clearTimeout(t)));
        this.beforeDismiss = () => this.confirmDiscard();
        // The list is placed from its input, so it closes if that moves, but
        // not while its own items scroll.
        useExternalListener(window, "scroll", (ev) => {
            if (this.state.suggest && !ev.target?.closest?.(".o_sb_pd_suggest")) {
                this.state.suggest = null;
            }
        }, { capture: true });
    }

    /** Start from a draft line, or from items a shortage asks for. */
    async prefill() {
        if (this.props.lineId) {
            const data = await this.orm.call("odin.stock.book", "book_purchase_load", [this.props.lineId]);
            this.state.supplier = data.supplier || null;
            this.state.supplierQuery = data.supplier_query || "";
            this.state.ref = data.purchase_ref;
            this.state.date = data.date;
            this.state.paid = data.paid;
            this.state.edit = data.edit;
            this.state.replaceIds = data.replace_ids;
            this.state.lines = data.lines.map((row) => ({
                ...blankLine(), product: row.product, query: row.product.display_name, note: row.note,
                qty: row.qty ? String(row.qty) : "", cost: row.cost ? String(row.cost) : "",
                taxId: row.tax_id, taxTouched: Boolean(row.tax_id),
            }));
        } else if (this.props.items?.length) {
            this.state.lines = this.props.items.map((product) => ({
                ...blankLine(), product, query: product.display_name,
            }));
        } else {
            return;
        }
        this.ensureBlankLine();
        await Promise.all(this.state.lines.filter((line) => line.product).map((line) => this.loadDefaults(line)));
        if (this.state.ref && this.state.supplier?.id && !this.state.replaceIds.length) {
            await this.checkRef();
        }
        await this.loadTotals();
    }

    /** Where typing should start: the first thing still missing at the top. */
    get firstCell() {
        if (!this.state.supplier) {
            return "supplier";
        }
        if (!this.state.ref) {
            return "ref";
        }
        const line = this.items.find((l) => this.lineProblem(l));
        return line ? `${this.lineProblem(line)}-${line.key}` : "paid";
    }

    get title() {
        if (this.state.edit) {
            return _t("Edit purchase");
        }
        return this.props.lineId ? _t("Complete purchase") : _t("Add purchase");
    }

    later(fn, ms = 0) {
        const timer = setTimeout(() => {
            this.timers.delete(timer);
            fn();
        }, ms);
        this.timers.add(timer);
    }

    /** ``data-pd="qty-12"``: the cell to go to; a new row's after it is drawn. */
    focus(name) {
        const go = () => {
            const el = this.root.el?.querySelector(`[data-pd="${name}"]`);
            el?.focus();
            el?.select?.();
            return Boolean(el);
        };
        // At once where the cell is there, so fast typing lands in it.
        if (!go()) {
            this.later(go);
        }
    }

    // ------------------------------------------------------------ what is entered
    isBlank(line) {
        return isBlank(line);
    }

    get items() {
        return this.state.lines.filter((line) => !isBlank(line));
    }

    get fingerprint() {
        const { supplierQuery, ref, date, paid } = this.state;
        return JSON.stringify([supplierQuery, ref, date, paid, this.items.map((line) => (
            [line.product?.id, line.query, line.qty, line.cost, line.taxId, line.note]))]);
    }

    get dirty() {
        return this.fingerprint !== this.snapshot;
    }

    async confirmDiscard() {
        if (!this.dirty) {
            return true;
        }
        return new Promise((resolve) => {
            this.dialogService.add(ConfirmationDialog, {
                title: _t("Discard these changes?"),
                body: _t("What you typed has not been saved."),
                confirmLabel: _t("Discard"), cancelLabel: _t("Keep editing"),
                confirm: () => resolve(true), cancel: () => resolve(false),
            }, { onClose: () => resolve(false) });
        });
    }

    lineProblem(line) {
        if (!line.product) {
            return "product";
        }
        if (!(parseAmount(line.qty, NaN) > 0)) {
            return "qty";
        }
        if (!(parseAmount(line.cost, NaN) > 0)) {
            return "cost";
        }
        return line.taxId ? null : "tax";
    }

    /** At least one line must be stock; what is not goes on the bill only. */
    get hasStock() {
        return this.items.some((line) => line.product?.stock);
    }

    /** The first thing still missing: [cell, message]. */
    get problem() {
        if (!this.state.supplier) {
            return ["supplier", _t("Pick the supplier.")];
        }
        if (!this.state.ref.trim()) {
            return ["ref", _t("Type the LPO or invoice number.")];
        }
        if (this.state.refProblem) {
            return ["ref", this.state.refProblem];
        }
        if (!this.state.date) {
            return ["date", _t("Type the date.")];
        }
        if (this.state.date > this.state.options.today) {
            return ["date", _t("Goods received cannot be dated ahead.")];
        }
        if (!this.items.length) {
            return [`product-${this.state.lines[0].key}`, _t("Add at least one item.")];
        }
        const firstProblemLine = this.items.find((line) => this.lineProblem(line));
        if (!firstProblemLine && !this.hasStock) {
            return [`product-${this.state.lines[0].key}`, _t("Add at least one item kept in stock. A purchase with no stock in it is a bill: enter it in Accounting.")];
        }
        for (const [index, line] of this.items.entries()) {
            const what = this.lineProblem(line);
            if (what) {
                const words = { product: _t("pick the item from the list"), qty: _t("type the quantity"),
                                cost: _t("type the cost per unit"), tax: _t("choose the VAT") };
                return [`${what}-${line.key}`, _t("Line %(n)s: %(what)s.", { n: index + 1, what: words[what] })];
            }
        }
        if (!this.state.paid) {
            return ["paid", _t("Say where it was paid from, or that it is not paid yet.")];
        }
        return null;
    }

    missing(line, field) {
        return this.state.tried && !isBlank(line) && this.lineProblem(line) === field;
    }

    // ------------------------------------------------------------ supplier
    async onSupplierInput(ev) {
        this.anchor = ev.target;
        const text = ev.target.value;
        this.state.supplierQuery = text;
        this.state.supplier = null;
        this.state.refProblem = "";
        if (!text.trim()) {
            this.state.suggest = null;
            return;
        }
        const { items, can_create } = await this.orm.call("odin.stock.book", "book_purchase_suppliers", [text]);
        if (this.state.supplierQuery !== text) {
            return;
        }
        const options = items.map((item) => ({ ...item, label: item.name }));
        if (can_create) {
            options.push({ create: text.trim(), label: _t('Create "%s" as a new supplier', text.trim()) });
        }
        // A new supplier is chosen on purpose, never by typing a name wrong
        // and pressing Enter: with no match, nothing starts selected.
        this.state.suggest = options.length
            ? { kind: "supplier", items: options, index: items.length ? 0 : -1, empty: !items.length } : null;
    }

    /** The list sits under its input, or above it where there is no room below. */
    get suggestStyle() {
        const rect = this.anchor?.getBoundingClientRect();
        if (!rect) {
            return "";
        }
        const width = Math.max(rect.width, 340);
        const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
        const below = window.innerHeight - rect.bottom;
        const place = below < 200 && rect.top > below
            ? `bottom: ${window.innerHeight - rect.top + 2}px;`
            : `top: ${rect.bottom + 2}px;`;
        return `${place} left: ${left}px; width: ${width}px;`;
    }

    amount(value) {
        return Number(value || 0).toLocaleString("en-KE", { maximumFractionDigits: 2 });
    }

    pickSupplier(item) {
        this.state.supplier = item.create ? { id: false, name: item.create } : { id: item.id, name: item.name };
        this.state.supplierQuery = this.state.supplier.name;
        this.state.suggest = null;
        this.refreshDefaults();
        this.checkRef();
        this.focus(this.state.ref ? `product-${this.state.lines[0].key}` : "ref");
    }

    /** VAT and last cost depend on the supplier; VAT chosen by hand stays. */
    refreshDefaults() {
        for (const line of this.state.lines) {
            if (line.product) {
                this.loadDefaults(line);
            }
        }
    }

    async loadDefaults(line) {
        const product = line.product;
        const defaults = await this.orm.call("odin.stock.book", "book_purchase_item", [product.id],
            { partner_id: this.state.supplier?.id || false });
        if (line.product !== product) {
            return;
        }
        line.lastCost = defaults.last_cost;
        if (!line.taxTouched) {
            line.taxId = defaults.tax_id;
        }
        this.scheduleTotals();
    }

    // ------------------------------------------------------------ number
    onRefInput(ev) {
        this.state.ref = ev.target.value;
        this.state.refProblem = "";
    }

    onRefKey(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            ev.target.blur();
            this.focus(`product-${this.state.lines[0].key}`);
        }
    }

    onPaid(ev) {
        this.state.paid = ev.target.value;
    }

    onPaidKey(ev) {
        if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey) {
            ev.preventDefault();
            this.root.el?.closest(".modal-content")?.querySelector(".o_sb_pd_save")?.focus();
        }
    }

    /** Told as soon as the number is typed, not after every item is. */
    async checkRef() {
        const ref = this.state.ref.trim();
        const seq = ++this.refSeq;
        if (!ref || !this.state.supplier?.id) {
            this.state.refProblem = "";
            return;
        }
        // The purchase's own number, while it is being edited, is not a duplicate.
        const problem = await this.orm.call("odin.stock.book", "book_purchase_check",
            [this.state.supplier.id, ref], { ignore_ids: this.state.replaceIds });
        if (seq === this.refSeq) {
            this.state.refProblem = problem || "";
        }
    }

    // ------------------------------------------------------------ lines
    async onItemInput(line, ev) {
        this.anchor = ev.target;
        const text = ev.target.value;
        line.query = text;
        line.product = null;
        line.amount = null;
        line.taxId = line.taxTouched ? line.taxId : false;
        this.scheduleTotals();
        if (!text.trim()) {
            this.state.suggest = null;
            return;
        }
        const items = await this.orm.call("odin.stock.book", "book_purchase_products", [text],
            { warehouse_id: this.props.warehouseId || false });
        if (line.query !== text) {
            return;
        }
        this.state.suggest = items.length
            ? { kind: "item", key: line.key, items: items.map((item) => ({ ...item, label: item.display_name })),
                index: Math.max(items.findIndex((item) => !item.tracked), 0) }
            : { kind: "item", key: line.key, items: [], index: -1, none: true };
    }

    /** Enter on the empty next item: the items are done, on to how it was paid. */
    onItemKey(line, ev) {
        if (this.state.suggest) {
            this.onSuggestKey(ev, (item) => this.pickItem(line, item));
        } else if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey && !line.query.trim()) {
            ev.preventDefault();
            this.focus("paid");
        }
    }

    pickItem(line, item) {
        if (item.tracked) {
            return;
        }
        line.product = item;
        line.query = item.display_name;
        this.state.suggest = null;
        this.loadDefaults(line);
        this.ensureBlankLine();
        this.focus(`qty-${line.key}`);
    }

    ensureBlankLine() {
        const last = this.state.lines[this.state.lines.length - 1];
        if (!isBlank(last)) {
            this.state.lines.push(blankLine());
        }
    }

    onNumber(line, field, ev) {
        line[field] = ev.target.value;
        this.ensureBlankLine();
        this.scheduleTotals();
    }

    onTax(line, ev) {
        line.taxId = Number(ev.target.value) || false;
        line.taxTouched = true;
        this.scheduleTotals();
    }

    removeLine(line) {
        this.state.lines = this.state.lines.filter((other) => other.key !== line.key);
        if (!this.state.lines.length) {
            this.state.lines.push(blankLine());
        }
        this.ensureBlankLine();
        this.scheduleTotals();
    }

    /** Enter moves on through the row, then to the next item. */
    onCellKey(line, field, ev) {
        if (ev.key !== "Enter" || ev.ctrlKey || ev.metaKey) {
            return;
        }
        ev.preventDefault();
        if (field === "qty") {
            this.focus(`cost-${line.key}`);
        } else {
            this.ensureBlankLine();
            const index = this.state.lines.findIndex((other) => other.key === line.key);
            this.focus(`product-${this.state.lines[index + 1]?.key}`);
        }
    }

    // ------------------------------------------------------------ the suggestion list
    onSuggestKey(ev, pick) {
        const suggest = this.state.suggest;
        if (!suggest) {
            return;
        }
        if ((ev.key === "ArrowDown" || ev.key === "ArrowUp") && suggest.items.length) {
            ev.preventDefault();
            const step = ev.key === "ArrowDown" ? 1 : -1;
            suggest.index = (suggest.index + step + suggest.items.length) % suggest.items.length;
        } else if (ev.key === "Enter" || ev.key === "Tab") {
            const item = suggest.items[suggest.index];
            if (ev.key === "Enter") {
                ev.preventDefault();
                if (ev.ctrlKey || ev.metaKey) {
                    return;
                }
            }
            // Tab only moves on from a new supplier; creating one takes Enter or a click.
            if (item && !(ev.key === "Tab" && item.create)) {
                ev.stopPropagation();
                pick(item);
            }
        } else if (ev.key === "Escape") {
            // Closes the list, not the window.
            ev.stopPropagation();
            ev.preventDefault();
            this.state.suggest = null;
        }
    }

    onBlurSuggest() {
        // Only the list this cell had: a newer one, for the next cell, stays.
        const suggest = this.state.suggest;
        this.later(() => {
            if (this.state.suggest === suggest && !this.root.el?.querySelector(".o_sb_pd_suggest:hover")) {
                this.state.suggest = null;
            }
        }, 120);
    }

    // ------------------------------------------------------------ totals
    scheduleTotals() {
        clearTimeout(this.totalsTimer);
        this.timers.delete(this.totalsTimer);
        this.totalsTimer = setTimeout(() => this.loadTotals(), 200);
        this.timers.add(this.totalsTimer);
    }

    get completeLines() {
        return this.items.filter((line) => !this.lineProblem(line));
    }

    linePayload(line) {
        return { product_id: line.product.id, qty: parseAmount(line.qty), cost: parseAmount(line.cost), tax_id: line.taxId,
                 note: line.note.trim() };
    }

    /** By Odoo's own tax computation: the figures the order will show. */
    async loadTotals() {
        const seq = ++this.totalsSeq;
        const lines = this.completeLines;
        for (const line of this.state.lines) {
            if (!lines.includes(line)) {
                line.amount = null;
            }
        }
        if (!lines.length) {
            this.state.totals = null;
            return;
        }
        const totals = await this.orm.call("odin.stock.book", "book_purchase_totals",
            [lines.map((line) => this.linePayload(line))], { partner_id: this.state.supplier?.id || false });
        if (seq !== this.totalsSeq) {
            return;
        }
        lines.forEach((line, index) => (line.amount = totals.lines[index]));
        this.state.totals = totals;
    }

    money(value) {
        return (value || 0).toLocaleString("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    get saveLabel() {
        if (!this.state.options.can_post) {
            return _t("Save for posting");
        }
        return this.state.totals ? _t("Save and receive · %s", this.money(this.state.totals.total)) : _t("Save and receive");
    }

    get saveHelp() {
        return this.state.options.can_post
            ? _t("Order, stock, bill and payment, all at once. Final once saved. Ctrl+Enter saves.")
            : _t("Waits on the book for a supervisor, who posts it with its bill and payment.");
    }

    // ------------------------------------------------------------ save
    async save() {
        if (this.state.saving) {
            return;
        }
        this.state.tried = true;
        this.state.error = "";
        const problem = this.problem;
        if (problem) {
            this.focus(problem[0]);
            return;
        }
        this.state.saving = true;
        try {
            const result = await this.orm.call("odin.stock.book", "book_purchase_save", [{
                warehouse_id: this.props.warehouseId || false,
                partner_id: this.state.supplier.id, partner_name: this.state.supplier.name,
                purchase_ref: this.state.ref.trim(), date: this.state.date,
                pay_journal_id: this.state.paid === "none" ? false : Number(this.state.paid),
                lines: this.items.map((line) => this.linePayload(line)),
            }], { replace_ids: this.state.replaceIds });
            this.props.onSaved(result);
            this.props.close();
        } catch (error) {
            // Nothing was saved: the window stays, with what was typed.
            if (error.data?.name?.endsWith("Error") && !error.data.name.endsWith("UserError")
                && !error.data.name.endsWith("ValidationError")) {
                throw error;
            }
            this.state.error = error.data?.message || error.message || _t("It could not be saved.");
        } finally {
            this.state.saving = false;
        }
    }

    async discard() {
        if (await this.confirmDiscard()) {
            this.props.close();
        }
    }
}
