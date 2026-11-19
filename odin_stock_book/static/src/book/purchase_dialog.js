import { Component, onMounted, onWillStart, onWillUnmount, useExternalListener, useRef, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { CustomerChoiceDialog } from "./customer_choice_dialog";
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
    // Sales: the customer's pricelist price last put in the box. While the box
    // still holds it, a new quantity, customer or day prices the line again,
    // as a sales order does; a price typed over it stays.
    priced: null, pricelist: "", keepPrice: false, priceSeq: 0,
});
const isBlank = (line) => !line.product && !line.query && !line.qty && !line.cost;
// A KRA PIN as KRA writes it: A (an individual) or P (anyone else), nine digits and a letter.
const KRA_PIN = /^[AP][0-9]{9}[A-Z]$/;
export const compactPin = (text) => (text || "").replace(/\s+/g, "").toUpperCase();

export const SERVER = {
    purchase: { options: "book_purchase_options", partners: "book_purchase_suppliers", products: "book_purchase_products",
                item: "book_purchase_item", check: "book_purchase_check", totals: "book_purchase_totals",
                load: "book_purchase_load", save: "book_purchase_save" },
    sale: { options: "book_sale_options", partners: "book_sale_customers", products: "book_sale_products",
            item: "book_sale_item", check: "book_sale_check", totals: "book_sale_totals",
            load: "book_sale_load", save: "book_sale_save" },
    bill: { options: "book_bill_options", partners: "book_bill_suppliers", check: "book_bill_check",
            totals: "book_bill_totals", save: "book_bill_save" },
};

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
        // "purchase", "sale" or "bill": the same window, and Odoo's own documents for each.
        kind: { type: String, optional: true },
        // What to start from when it is no longer a draft: an undone purchase.
        loaded: { type: Object, optional: true },
        onSaved: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.dialogService = useService("dialog");
        this.root = useRef("root");
        this.isSale = this.props.kind === "sale";
        // Asks for the partner's KRA PIN, kept on the partner.
        this.asksPin = this.isSale;
        this.server = SERVER[this.props.kind] || SERVER.purchase;
        this.words = this.isSale ? {
            partner: _t("Customer"), partnerHint: _t("Type to find the customer"), noPartner: _t("No customer matches."),
            ref: _t("Invoice no."), date: _t("Date delivered"), paid: _t("Paid into"), cost: _t("Unit price"),
            pin: _t("KRA PIN"), cu: _t("CU invoice no."), terms: _t("Payment terms"), due: _t("Due date"),
            document: _t("invoice"), notStock: _t("Not stock: on the invoice only"),
            noteHint: _t("What for? Shows on the invoice"), itemHint: _t("Code or name, e.g. bleach or delivery"),
        } : {
            partner: _t("Supplier"), partnerHint: _t("Type to find the supplier"), noPartner: _t("No supplier matches."),
            ref: _t("LPO / invoice no."), date: _t("Date received"), paid: _t("Paid from"), cost: _t("Unit cost"),
            document: _t("bill"), notStock: _t("Not stock: on the bill only"),
            noteHint: _t("What for? Shows on the bill"), itemHint: _t("Code or name, e.g. salt or transport"),
        };
        this.state = useState({
            options: { taxes: [], journals: [], payment_terms: [], today: "", can_post: false, currency: "", new_pricelist: "" },
            supplier: null, supplierQuery: "", ref: "", date: "", refProblem: "", paid: "",
            // Sales: the customer's KRA PIN and terms, filled in from the customer
            // and kept there when typed; the CU invoice number the invoice prints.
            pin: "", terms: "", cu: "", due: "",
            lines: [this.newLine()], suggest: null, totals: null, saving: false, error: "", tried: false,
            edit: false, replaceIds: [],
        });
        this.totalsSeq = 0;
        this.refSeq = 0;
        this.timers = new Set();
        onWillStart(async () => {
            this.state.options = await this.orm.call("odin.stock.book", this.server.options, []);
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
        if (this.props.lineId || this.props.loaded) {
            const data = this.props.loaded || await this.orm.call("odin.stock.book", this.server.load, [this.props.lineId]);
            this.state.supplier = data.supplier || null;
            this.state.supplierQuery = data.supplier_query || "";
            this.state.ref = data.purchase_ref;
            this.state.date = data.date;
            this.state.paid = data.paid;
            this.state.edit = data.edit;
            this.state.replaceIds = data.replace_ids;
            if (this.isSale) {
                this.state.pin = data.partner_vat || "";
                this.state.terms = data.payment_term_id ? String(data.payment_term_id) : "";
                this.state.cu = data.cu_invoice_no || "";
            }
            this.state.lines = data.lines.map((row) => ({
                ...blankLine(), product: row.product, query: row.product.display_name, note: row.note,
                qty: row.qty ? String(row.qty) : "", cost: row.cost ? String(row.cost) : "",
                taxId: row.tax_id, taxTouched: Boolean(row.tax_id),
                // An entered sale keeps its prices, a free item's 0 included.
                keepPrice: Boolean(data.edit || data.keep_prices),
            }));
        } else if (this.props.items?.length) {
            this.state.lines = this.props.items.map((product) => ({
                ...blankLine(), product, query: product.display_name,
            }));
        } else {
            await this.loadDue();
            return;
        }
        this.ensureBlankLine();
        await this.loadDue();
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
        if (this.isSale) {
            return this.state.edit ? _t("Edit sale") : this.props.lineId ? _t("Complete sale") : _t("Add sale");
        }
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
    newLine() {
        return blankLine();
    }

    isBlank(line) {
        return isBlank(line);
    }

    /** The cell a line starts in. */
    firstLineCell(line) {
        return `product-${line?.key}`;
    }

    get items() {
        return this.state.lines.filter((line) => !this.isBlank(line));
    }

    get fingerprint() {
        const { supplierQuery, ref, date, paid, pin, terms, cu } = this.state;
        return JSON.stringify([supplierQuery, ref, date, paid, pin, terms, cu, this.items.map((line) => (
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
        // A sale may be free, a replacement say, when 0 is typed.
        const cost = parseAmount(line.cost, NaN);
        if (!(this.isSale ? cost >= 0 : cost > 0)) {
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
            return ["supplier", this.isSale ? _t("Pick the customer.") : _t("Pick the supplier.")];
        }
        if (!this.state.ref.trim()) {
            return ["ref", this.isSale ? _t("Type the invoice number.") : _t("Type the LPO or invoice number.")];
        }
        if (this.state.refProblem) {
            return ["ref", this.state.refProblem];
        }
        if (!this.state.date) {
            return ["date", _t("Type the date.")];
        }
        if (this.state.date > this.state.options.today) {
            return ["date", this.isSale ? _t("The delivery date cannot be in the future.")
                : _t("The date received cannot be in the future.")];
        }
        if (this.pinProblem) {
            return ["pin", this.pinProblem];
        }
        if (!this.items.length) {
            return [`product-${this.state.lines[0].key}`, _t("Add at least one item.")];
        }
        const firstProblemLine = this.items.find((line) => this.lineProblem(line));
        if (!firstProblemLine && !this.hasStock) {
            return [`product-${this.state.lines[0].key}`, this.isSale ? _t("Add at least one item kept in stock. A sale with no stock in it is an invoice: enter it in Accounting.")
                : _t("Add at least one item kept in stock. A purchase with no stock in it is a bill: enter it with Add bill.")];
        }
        for (const [index, line] of this.items.entries()) {
            const what = this.lineProblem(line);
            if (what) {
                const words = { product: _t("pick the item from the list"), qty: _t("type the quantity"),
                                cost: this.isSale ? _t("type the price per unit") : _t("type the cost per unit"), tax: _t("choose the VAT") };
                return [`${what}-${line.key}`, _t("Line %(n)s: %(what)s.", { n: index + 1, what: words[what] })];
            }
        }
        if (!this.state.paid) {
            return ["paid", this.isSale ? _t("Say where it was paid into, or that it is not paid yet.")
                : _t("Say where it was paid from, or that it is not paid yet.")];
        }
        return null;
    }

    /** A PIN typed must be a KRA PIN; the one the customer has stays as it is (a foreign tax ID, say). */
    get pinProblem() {
        const pin = compactPin(this.state.pin);
        if (!this.asksPin || !pin || pin === compactPin(this.state.supplier?.vat) || KRA_PIN.test(pin)) {
            return "";
        }
        return _t("%s is not a KRA PIN: A or P, nine digits and a letter, e.g. P052112729P.", this.state.pin.trim());
    }

    missing(line, field) {
        return this.state.tried && !this.isBlank(line) && this.lineProblem(line) === field;
    }

    // ------------------------------------------------------------ supplier
    async onSupplierInput(ev) {
        this.anchor = ev.target;
        const text = ev.target.value;
        this.state.supplierQuery = text;
        if (this.asksPin && this.state.supplier) {
            // Another customer: the PIN and terms were the last one's.
            this.state.pin = "";
            this.state.terms = "";
            this.loadDue();
        }
        this.state.supplier = null;
        this.state.refProblem = "";
        if (!text.trim()) {
            this.state.suggest = null;
            return;
        }
        const { items, can_create } = await this.orm.call("odin.stock.book", this.server.partners, [text]);
        if (this.state.supplierQuery !== text) {
            return;
        }
        // A branch by its company's name too, and with its prices and terms:
        // a wrong customer, Retail where the company's own was meant, shows here.
        const options = items.map((item) => ({ ...item, label: item.display_name || item.name, hint: this.customerHint(item) }));
        if (can_create) {
            options.push({
                create: text.trim(), label: this.isSale ? _t("Create “%s” as a new customer", text.trim()) : _t("Create “%s” as a new supplier", text.trim()),
                hint: this.isSale && this.state.options.new_pricelist ? _t("Prices: %s, as for every new customer", this.state.options.new_pricelist) : "",
            });
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

    /** A sale customer's prices and terms, under its name in the list. */
    customerHint(item) {
        if (!this.isSale) {
            return "";
        }
        const prices = item.pricelist ? _t("Prices: %s", item.pricelist) : "";
        return [prices, item.payment_term].filter(Boolean).join(" · ");
    }

    pickSupplier(item) {
        if (item.create && this.isSale) {
            // A customer typed in is asked about before it is made.
            this.state.suggest = null;
            this.state.supplierQuery = item.create;
            this.settleNewCustomer({ id: false, name: item.create }).then((settled) => settled || this.focus("supplier"));
            return;
        }
        const { label, hint, create, ...supplier } = item;
        this.setSupplier(create ? { id: false, name: create } : { ...supplier, vat: supplier.vat || "" });
    }

    /**
     * ``keepTyped``: the PIN and terms typed stay where the customer has none
     * (a customer settled on at saving). ``next``: the cell to go to, false
     * for none.
     */
    setSupplier(supplier, { keepTyped = false, next = null } = {}) {
        this.state.supplier = supplier;
        this.state.supplierQuery = supplier.display_name || supplier.name;
        this.state.suggest = null;
        if (this.asksPin) {
            // What the partner has, to keep or to fill in where it is missing.
            this.state.pin = supplier.vat || (keepTyped ? this.state.pin : "");
        }
        if (this.isSale) {
            this.state.terms = supplier.payment_term_id ? String(supplier.payment_term_id) : (keepTyped ? this.state.terms : "");
            this.loadDue();
        }
        this.refreshDefaults();
        this.checkRef();
        if (next !== false) {
            this.focus(next || (this.state.ref ? this.firstLineCell(this.state.lines[0]) : "ref"));
        }
    }

    /**
     * A customer typed in rather than picked, asked about before it is
     * created: the customers with the words of its name, and the one the KRA
     * PIN typed is already on, whose branch it most likely is. Picked from
     * them, the sale is priced at that customer's pricelist; added as a new
     * branch, at its company's. True once it is settled; the customer then in
     * the window may be another one, to look at before saving.
     */
    async settleNewCustomer(candidate, { next = null } = {}) {
        const current = () => this.state.supplier === candidate
            || (!this.state.supplier && this.state.supplierQuery === candidate.name);
        const pin = compactPin(this.state.pin);
        const found = await this.orm.call("odin.stock.book", "book_sale_customer_matches", [candidate.name], { pin });
        if (!current()) {
            return false;
        }
        const owner = found.owner;
        const settled = owner ? candidate.parentId === owner.id
            : Boolean(candidate.parentId || candidate.decided || !found.matches.length);
        if (settled) {
            if (candidate !== this.state.supplier) {
                this.setSupplier({ ...candidate, decided: true, pricelist: this.state.options.new_pricelist }, { next });
            }
            return true;
        }
        const choice = await new Promise((resolve) => {
            this.dialogService.add(CustomerChoiceDialog, {
                name: candidate.name, matches: found.matches, owner, branches: found.branches, pin: found.pin,
                newPricelist: this.state.options.new_pricelist, choose: resolve,
            }, { onClose: () => resolve(null) });
        });
        if (!choice || !current()) {
            return false;
        }
        const keep = { keepTyped: true, next };
        if (choice.kind === "pick") {
            this.setSupplier({ ...choice.customer }, keep);
        } else if (choice.kind === "branch") {
            this.setSupplier({
                id: false, name: candidate.name, display_name: `${owner.display_name}, ${candidate.name}`,
                parentId: owner.id, parentName: owner.display_name, vat: owner.vat, decided: true,
                payment_term_id: owner.payment_term_id, payment_term: owner.payment_term, pricelist: owner.pricelist,
            }, keep);
        } else {
            this.setSupplier({ id: false, name: candidate.name, decided: true, pricelist: this.state.options.new_pricelist }, keep);
        }
        return true;
    }

    /** The PIN of a customer still to be created may be a customer's already. */
    onPinChange() {
        const supplier = this.state.supplier;
        if (this.isSale && supplier && !supplier.id && KRA_PIN.test(compactPin(this.state.pin))) {
            this.settleNewCustomer(supplier, { next: false });
        }
    }

    /** Whose prices the sale is at: a new branch is priced as its company. */
    get pricingPartnerId() {
        return this.state.supplier?.id || this.state.supplier?.parentId || false;
    }

    /** Which prices the lines are at, said plainly, so a wrong customer shows before saving. */
    get pricesLabel() {
        const supplier = this.state.supplier;
        if (!this.isSale || !supplier) {
            return "";
        }
        const pricelist = supplier.pricelist || _t("each item's Sales Price");
        if (supplier.parentId) {
            return _t("Prices: %(pricelist)s · a new branch of %(company)s", { pricelist, company: supplier.parentName });
        }
        return supplier.id ? _t("Prices: %s", pricelist) : _t("Prices: %s · a new customer", pricelist);
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
        const seq = ++line.priceSeq;
        const kwargs = { partner_id: this.pricingPartnerId };
        if (this.isSale) {
            Object.assign(kwargs, { qty: parseAmount(line.qty) || 1, date: this.state.date || false });
        }
        const defaults = await this.orm.call("odin.stock.book", this.server.item, [product.id], kwargs);
        if (line.product !== product || line.priceSeq !== seq) {
            return;
        }
        line.lastCost = defaults.last_cost;
        if (!line.taxTouched) {
            line.taxId = defaults.tax_id;
        }
        if (this.isSale && defaults.price !== undefined) {
            const untouched = line.cost === "" || parseAmount(line.cost, NaN) === line.priced;
            if (!line.keepPrice && untouched) {
                line.cost = String(defaults.price);
                line.priced = defaults.price;
            }
            line.pricelist = defaults.pricelist;
        }
        this.scheduleTotals();
    }

    /** A sale line's price follows its quantity (minimum quantities). */
    repriceSoon(line) {
        clearTimeout(line.repriceTimer);
        line.repriceTimer = setTimeout(() => line.product && this.loadDefaults(line), 250);
    }

    onDate(ev) {
        this.state.date = ev.target.value;
        if (this.isSale) {
            this.refreshDefaults();
            this.loadDue();
        }
    }

    // ------------------------------------------------------------ PIN, terms, CU number
    onTerms(ev) {
        this.state.terms = ev.target.value;
        this.loadDue();
    }

    /** "None" only for a customer without terms: terms on file are changed, not taken off, here. */
    get canLeaveTermsEmpty() {
        return !this.state.supplier?.payment_term_id;
    }

    /** The day the invoice falls due, as Odoo will date it from the terms and the invoice date. */
    async loadDue() {
        if (!this.isSale) {
            return;
        }
        const seq = (this.dueSeq = (this.dueSeq || 0) + 1);
        const due = await this.orm.call("odin.stock.book", "book_sale_due", [],
            { payment_term_id: Number(this.state.terms) || false, date: this.state.date || false });
        if (seq === this.dueSeq) {
            this.state.due = due;
        }
    }

    get dueLabel() {
        if (!this.state.due) {
            return "—";
        }
        const [y, m, d] = this.state.due.split("-").map(Number);
        const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1];
        return `${d} ${month} ${y}`;
    }

    onHeadKey(next, ev) {
        if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey) {
            ev.preventDefault();
            this.focus(next);
        }
    }

    /** What the price box says on hover: where its price came from. */
    priceTitle(line) {
        if (!this.isSale || !line.pricelist || line.priced === null) {
            return this.words.cost;
        }
        return parseAmount(line.cost, NaN) === line.priced
            ? _t("%(pricelist)s price. Type over it to change it.", { pricelist: line.pricelist })
            : _t("Typed in. The %(pricelist)s price is %(price)s.", { pricelist: line.pricelist, price: this.amount(line.priced) });
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
            this.focus(this.firstLineCell(this.state.lines[0]));
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
        const problem = await this.orm.call("odin.stock.book", this.server.check,
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
        const items = await this.orm.call("odin.stock.book", this.server.products, [text],
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
        if (!this.isBlank(last)) {
            this.state.lines.push(this.newLine());
        }
    }

    onNumber(line, field, ev) {
        line[field] = ev.target.value;
        this.ensureBlankLine();
        this.scheduleTotals();
        if (this.isSale && field === "qty" && line.product) {
            this.repriceSoon(line);
        }
    }

    onTax(line, ev) {
        line.taxId = Number(ev.target.value) || false;
        line.taxTouched = true;
        this.scheduleTotals();
    }

    removeLine(line) {
        this.state.lines = this.state.lines.filter((other) => other.key !== line.key);
        if (!this.state.lines.length) {
            this.state.lines.push(this.newLine());
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
            this.focus(this.firstLineCell(this.state.lines[index + 1]));
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
        const totals = await this.orm.call("odin.stock.book", this.server.totals,
            [lines.map((line) => this.linePayload(line))], { partner_id: this.pricingPartnerId });
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
        if (this.isSale) {
            return this.state.totals ? _t("Save and deliver · %s", this.money(this.state.totals.total)) : _t("Save and deliver");
        }
        return this.state.totals ? _t("Save and receive · %s", this.money(this.state.totals.total)) : _t("Save and receive");
    }

    get saveHelp() {
        if (this.isSale) {
            return this.state.options.can_post
                ? (this.state.paid === "none"
                    ? _t("Creates the order, delivery and invoice, with the invoice left unpaid. Final once saved. Ctrl+Enter saves.")
                    : _t("Creates the order, delivery, invoice and payment. Final once saved. Ctrl+Enter saves."))
                : _t("Waits on the book for a supervisor, who posts it with its invoice.");
        }
        return this.state.options.can_post
            ? (this.state.paid === "none"
                ? _t("Creates the order, stock and bill, with the bill left unpaid. Final once saved. Ctrl+Enter saves.")
                : _t("Creates the order, stock, bill and payment. Final once saved. Ctrl+Enter saves."))
            : _t("Waits on the book for a supervisor, who posts it with its bill.");
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
        const supplier = this.state.supplier;
        if (this.isSale && !supplier.id) {
            // Asked once more with the PIN as it is now. Another customer
            // chosen prices the lines again: they are looked at before saving.
            if (this.settling) {
                return;
            }
            this.settling = true;
            try {
                if (!(await this.settleNewCustomer(supplier, { next: false })) || this.state.supplier !== supplier) {
                    return;
                }
            } finally {
                this.settling = false;
            }
        }
        this.state.saving = true;
        try {
            const result = await this.orm.call("odin.stock.book", this.server.save, [this.savePayload()],
                this.saveKwargs());
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

    savePayload() {
        return {
            warehouse_id: this.props.warehouseId || false,
            partner_id: this.state.supplier.id, partner_name: this.state.supplier.name,
            purchase_ref: this.state.ref.trim(), date: this.state.date,
            pay_journal_id: this.state.paid === "none" ? false : Number(this.state.paid),
            lines: this.items.map((line) => this.linePayload(line)),
            ...(this.isSale ? {
                partner_vat: compactPin(this.state.pin), payment_term_id: Number(this.state.terms) || false,
                cu_invoice_no: this.state.cu.trim(),
                // A customer typed in: as a branch of this one, or a new customer all the same.
                partner_parent_id: this.state.supplier.parentId || false,
                partner_new: Boolean(this.state.supplier.decided),
            } : {}),
        };
    }

    saveKwargs() {
        return { replace_ids: this.state.replaceIds };
    }

    async discard() {
        if (await this.confirmDiscard()) {
            this.props.close();
        }
    }
}
