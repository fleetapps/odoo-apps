import { onWillUnmount } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { compactPin, PurchaseDialog, SERVER } from "./purchase_dialog";
import { parseAmount } from "./utils";

let billKey = 0;
/** A bill line: what it was for, the account it is charged to, and its amount. */
const billLine = (defaults = {}) => ({
    key: `b${++billKey}`, name: "", account: defaults.account || null, accountQuery: defaults.account?.label || "",
    accountTouched: false, qty: "", cost: "", taxId: defaults.tax_id || false, taxTouched: false, amount: null,
});

let fileKey = 0;

/**
 * Add bill: a purchase that is not stock, already paid. Printing paper,
 * electricity, a repair. Odoo's vendor bill, without a product or an order:
 * the vendor, the number on the paper, a line for each thing it is for,
 * charged to an account, with its VAT, the scan, and where it was paid from.
 * Saving posts the bill and its payment; nothing goes on the book.
 *
 * The same window as Add purchase: the supplier, the number check, the
 * totals and the keys work as they do there.
 */
export class BillDialog extends PurchaseDialog {
    static template = "odin_stock_book.BillDialog";

    setup() {
        super.setup();
        this.isBill = true;
        this.asksPin = true;
        this.server = SERVER.bill;
        this.words = {
            partner: _t("Supplier"), partnerHint: _t("Type to find the supplier"), noPartner: _t("No supplier matches."),
            ref: _t("Bill / receipt no."), date: _t("Bill date"), paid: _t("Paid from"), cost: _t("Unit price"),
            pin: _t("Supplier KRA PIN"), cu: _t("CU invoice no."), document: _t("bill"),
        };
        Object.assign(this.state, { cuProblem: "", files: [], fileError: "", dragging: false });
        // What a picked vendor's new lines start with: its usual account and VAT.
        this.vendorDefaults = null;
        this.cuSeq = 0;
        onWillUnmount(() => this.state.files.forEach((file) => URL.revokeObjectURL(file.url)));
    }

    get title() {
        return _t("Add bill");
    }

    /** One journal to pay from: it is chosen already. */
    async prefill() {
        await super.prefill();
        const journals = this.state.options.journals;
        if (journals.length === 1) {
            this.state.paid = String(journals[0].id);
        }
    }

    // ------------------------------------------------------------ what is entered
    newLine() {
        return billLine(this.vendorDefaults || {});
    }

    isBlank(line) {
        return !line.name.trim() && !line.qty && !line.cost;
    }

    firstLineCell(line) {
        return `name-${line?.key}`;
    }

    get fingerprint() {
        const { supplierQuery, ref, date, paid, pin, cu, files } = this.state;
        return JSON.stringify([supplierQuery, ref, date, paid, pin, cu, files.map((file) => file.key),
            this.items.map((line) => [line.name, line.account?.id, line.qty, line.cost, line.taxId])]);
    }

    /** The quantity may be left empty: one. */
    qtyOf(line) {
        return line.qty.trim() === "" ? 1 : parseAmount(line.qty, NaN);
    }

    lineProblem(line) {
        if (!line.name.trim()) {
            return "name";
        }
        if (!line.account) {
            return "account";
        }
        if (!(this.qtyOf(line) > 0)) {
            return "qty";
        }
        if (!(parseAmount(line.cost, NaN) > 0)) {
            return "cost";
        }
        return line.taxId ? null : "tax";
    }

    get problem() {
        if (!this.state.supplier) {
            return ["supplier", _t("Pick the supplier.")];
        }
        if (!this.state.ref.trim()) {
            return ["ref", _t("Type the bill or receipt number.")];
        }
        if (this.state.refProblem) {
            return ["ref", this.state.refProblem];
        }
        if (!this.state.date) {
            return ["date", _t("Type the date on the bill.")];
        }
        if (this.state.date > this.state.options.today) {
            return ["date", _t("The bill date cannot be in the future.")];
        }
        if (this.pinProblem) {
            return ["pin", this.pinProblem];
        }
        if (this.state.cuProblem) {
            return ["cu", this.state.cuProblem];
        }
        if (!this.items.length) {
            return [this.firstLineCell(this.state.lines[0]), _t("Add a line: what the bill is for.")];
        }
        for (const [index, line] of this.items.entries()) {
            const what = this.lineProblem(line);
            if (what) {
                const words = { name: _t("say what it was for"), account: _t("choose the account from the list"),
                                qty: _t("type the quantity"), cost: _t("type the amount"), tax: _t("choose the VAT") };
                return [`${what}-${line.key}`, _t("Line %(n)s: %(what)s.", { n: index + 1, what: words[what] })];
            }
        }
        if (!this.state.paid) {
            return ["paid", _t("Say where it was paid from.")];
        }
        return null;
    }

    /**
     * Not a stop: KRA only allows a bill's VAT as input tax, and its cost as
     * an expense, when it is the supplier's eTIMS invoice. Said once there is
     * VAT on the bill and no CU number for it.
     */
    get vatNote() {
        if (!this.state.totals?.vat || this.state.cu.trim()) {
            return "";
        }
        return _t("No CU invoice no.: KRA allows VAT as input tax only on the supplier's eTIMS invoice. It can be added on the bill later.");
    }

    // ------------------------------------------------------------ the supplier
    pickSupplier(item) {
        super.pickSupplier(item);
        this.vendorDefaults = null;
        if (this.state.supplier?.id) {
            this.loadVendor(this.state.supplier.id);
        }
    }

    /** A regular vendor's lines start on its usual account and VAT; what was chosen stays. */
    async loadVendor(partnerId) {
        const defaults = await this.orm.call("odin.stock.book", "book_bill_vendor", [partnerId]);
        if (this.state.supplier?.id !== partnerId || !defaults) {
            return;
        }
        this.vendorDefaults = { account: defaults.account || null, tax_id: defaults.tax_id || false };
        for (const line of this.state.lines) {
            if (!line.accountTouched && defaults.account) {
                line.account = defaults.account;
                line.accountQuery = defaults.account.label;
            }
            if (!line.taxTouched && defaults.tax_id && (!line.taxId || !line.accountTouched)) {
                line.taxId = defaults.tax_id;
            }
        }
        this.scheduleTotals();
    }

    // ------------------------------------------------------------ CU number
    onCuInput(ev) {
        this.state.cu = ev.target.value;
        this.state.cuProblem = "";
    }

    /** Told as soon as it is typed: a CU number is one invoice, entered once. */
    async checkCu() {
        const cu = this.state.cu.trim();
        const seq = ++this.cuSeq;
        if (!cu) {
            this.state.cuProblem = "";
            return;
        }
        const problem = await this.orm.call("odin.stock.book", "book_bill_cu_check", [cu]);
        if (seq === this.cuSeq) {
            this.state.cuProblem = problem || "";
        }
    }

    // ------------------------------------------------------------ lines
    onNameInput(line, ev) {
        line.name = ev.target.value;
        this.ensureBlankLine();
    }

    onNameKey(line, ev) {
        if (ev.key !== "Enter" || ev.ctrlKey || ev.metaKey) {
            return;
        }
        ev.preventDefault();
        if (!line.name.trim()) {
            this.focus("paid");
        } else {
            this.focus(line.account ? `qty-${line.key}` : `account-${line.key}`);
        }
    }

    async loadAccounts(line, text, anchor) {
        this.anchor = anchor;
        const items = await this.orm.call("odin.stock.book", "book_bill_accounts", [text],
            { partner_id: this.state.supplier?.id || false });
        if (line.accountQuery !== text) {
            return;
        }
        this.state.suggest = items.length
            ? { kind: "account", key: line.key, items, index: 0 }
            : { kind: "account", key: line.key, items: [], index: -1, none: true };
    }

    onAccountInput(line, ev) {
        const text = ev.target.value;
        line.accountQuery = text;
        line.account = null;
        line.accountTouched = true;
        this.loadAccounts(line, text, ev.target);
    }

    /** An empty account cell lists the accounts this vendor's bills use. */
    onAccountFocus(line, ev) {
        if (!line.account && !line.accountQuery.trim()) {
            this.loadAccounts(line, "", ev.target);
        }
    }

    onAccountKey(line, ev) {
        if (this.state.suggest?.kind === "account" && this.state.suggest.key === line.key) {
            this.onSuggestKey(ev, (item) => this.pickAccount(line, item));
        } else if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey) {
            ev.preventDefault();
            this.focus(`qty-${line.key}`);
        }
    }

    pickAccount(line, item) {
        line.account = item;
        line.accountQuery = item.label;
        line.accountTouched = true;
        this.state.suggest = null;
        // As Odoo's own bill line: the account's default VAT, else the vendor's usual.
        if (!line.taxTouched) {
            line.taxId = item.tax_id || this.vendorDefaults?.tax_id || line.taxId;
        }
        this.scheduleTotals();
        this.focus(`qty-${line.key}`);
    }

    /** Enter moves on through the row: quantity, amount, then the next line. */
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

    // ------------------------------------------------------------ totals
    /** A line is priced as soon as it has an amount and its VAT. */
    get completeLines() {
        return this.items.filter((line) => this.qtyOf(line) > 0 && parseAmount(line.cost, NaN) > 0 && line.taxId);
    }

    linePayload(line) {
        return { name: line.name.trim(), account_id: line.account?.id || false, qty: this.qtyOf(line),
                 cost: parseAmount(line.cost), tax_id: line.taxId };
    }

    get saveLabel() {
        return this.state.totals ? _t("Save and pay · %s", this.money(this.state.totals.total)) : _t("Save and pay");
    }

    get saveHelp() {
        return _t("Posts the bill with its scan and pays it in full. Final once saved. Ctrl+Enter saves.");
    }

    /** A bill is posted and paid when saved: it has no draft. */
    get offersDraft() {
        return false;
    }

    // ------------------------------------------------------------ the scan
    openFiles() {
        this.root.el?.querySelector(".o_sb_bd_file_input")?.click();
    }

    onFileInput(ev) {
        this.addFiles([...ev.target.files]);
        ev.target.value = "";
    }

    onDragOver(ev) {
        if ([...(ev.dataTransfer?.types || [])].includes("Files")) {
            ev.preventDefault();
            this.state.dragging = true;
        }
    }

    onDragLeave(ev) {
        if (!ev.currentTarget.contains(ev.relatedTarget)) {
            this.state.dragging = false;
        }
    }

    onDrop(ev) {
        ev.preventDefault();
        this.state.dragging = false;
        this.addFiles([...(ev.dataTransfer?.files || [])]);
    }

    /** PDFs and photos, read here and sent with the bill when it is saved. */
    async addFiles(files) {
        const { file_types: types, max_file_mb: maxMb, max_files: maxFiles } = this.state.options;
        this.state.fileError = "";
        for (const file of files) {
            if (this.state.files.length >= maxFiles) {
                this.state.fileError = _t("At most %s files on one bill.", maxFiles);
                break;
            }
            if (!types.includes(file.type)) {
                this.state.fileError = _t("%s is not a PDF or a photo (JPG, PNG).", file.name);
                continue;
            }
            if (file.size > maxMb * 1024 * 1024) {
                this.state.fileError = _t("%(name)s is larger than %(mb)s MB.", { name: file.name, mb: maxMb });
                continue;
            }
            const data = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
                reader.onerror = () => reject(reader.error);
                reader.readAsDataURL(file);
            }).catch(() => "");
            if (!data) {
                this.state.fileError = _t("%s could not be read.", file.name);
                continue;
            }
            this.state.files.push({ key: ++fileKey, name: file.name, size: file.size, type: file.type, data,
                                    url: URL.createObjectURL(file) });
        }
    }

    removeFile(file) {
        URL.revokeObjectURL(file.url);
        this.state.files = this.state.files.filter((other) => other.key !== file.key);
        this.state.fileError = "";
    }

    fileSize(file) {
        return file.size >= 1024 * 1024 ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(file.size / 1024)} KB`;
    }

    // ------------------------------------------------------------ save
    savePayload() {
        return {
            partner_id: this.state.supplier.id, partner_name: this.state.supplier.name,
            partner_vat: compactPin(this.state.pin), purchase_ref: this.state.ref.trim(), date: this.state.date,
            pay_journal_id: Number(this.state.paid) || false, cu_invoice_no: this.state.cu.trim(),
            lines: this.items.map((line) => this.linePayload(line)),
            files: this.state.files.map((file) => ({ name: file.name, data: file.data })),
        };
    }

    saveKwargs() {
        return {};
    }
}
