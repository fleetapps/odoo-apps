import { onWillUnmount, useState } from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { PurchaseDialog } from "@odin_stock_book/book/purchase_dialog";

let pageKey = 0;

/** A file as the server takes it: its name and its bytes in base64. */
function readAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    }).catch(() => "");
}

/** What the person can be told: an error raised on purpose on the server. */
function shownError(error) {
    const name = error?.data?.name || "";
    return name.endsWith("UserError") || name.endsWith("AccessError") || name.endsWith("ValidationError");
}

/**
 * Upload photo, in Add sale: the photos of the printed invoice are read, and
 * the window is filled in from them for the person to check. Lines left in
 * doubt are highlighted and wait for "Looks right" (or a correction) before
 * the sale can be saved; nothing is saved by reading. The photos go with
 * the sale when it is saved.
 *
 * Add purchase and Add bill are the same window, untouched: everything here
 * waits for the sale window's options to offer a photo.
 */
patch(PurchaseDialog.prototype, {
    setup() {
        super.setup(...arguments);
        this.photo = useState({
            id: false, files: [], unsent: [], busy: "", error: "", note: "", flags: [], total: null,
            choices: [], header: null, attempt: 0, read: false, dragging: false,
        });
        this.photoSeq = 0;
        onWillUnmount(() => this.photo.unsent.forEach((file) => URL.revokeObjectURL(file.url)));
    },

    get photoOptions() {
        return (this.isSale && this.state.options.photo) || null;
    },

    get photoBlocker() {
        return this.photoOptions?.blocker || "";
    },

    get photoAccept() {
        return (this.photoOptions?.types || []).join(",");
    },

    /** The window opened on a saved sale shows its photos, to compare. */
    async prefill() {
        await super.prefill(...arguments);
        if (!this.isSale) {
            return;
        }
        const saved = this.props.loaded?.photo
            || (this.props.lineId && await this.orm.call("odin.stock.book", "book_sale_photo_files", [this.props.lineId]));
        if (saved) {
            this.photo.id = saved.id;
            this.photo.files = saved.files.map((file) => ({ ...file, key: ++pageKey }));
        }
    },

    get fingerprint() {
        return JSON.stringify([super.fingerprint, this.photo?.id, this.photo?.files.map((file) => file.id)]);
    },

    // ------------------------------------------------------------ the photos
    openPhotoPicker() {
        this.root.el?.querySelector(".o_sb_ph_input")?.click();
    },

    onPhotoInput(ev) {
        const files = [...ev.target.files];
        ev.target.value = "";
        this.addPhotos(files);
    },

    onPhotoDragOver(ev) {
        if (this.photoOptions && !this.photoBlocker && [...(ev.dataTransfer?.types || [])].includes("Files")) {
            ev.preventDefault();
            this.photo.dragging = true;
        }
    },

    onPhotoDragLeave(ev) {
        if (!ev.currentTarget.contains(ev.relatedTarget)) {
            this.photo.dragging = false;
        }
    },

    onPhotoDrop(ev) {
        if (!this.photoOptions || this.photoBlocker) {
            return;
        }
        ev.preventDefault();
        this.photo.dragging = false;
        this.addPhotos([...(ev.dataTransfer?.files || [])]);
    },

    /** Photos picked or dropped: checked here, then read with the pages already uploaded. */
    async addPhotos(files) {
        if (!files.length || this.photo.busy) {
            return;
        }
        const { types, max_files: maxFiles, max_mb: maxMb } = this.photoOptions;
        this.photo.error = "";
        const ready = [];
        for (const file of files) {
            if (this.photo.files.length + ready.length >= maxFiles) {
                this.photo.error = _t("At most %s photos for one invoice.", maxFiles);
                break;
            }
            if (!types.includes(file.type)) {
                this.photo.error = _t("%s is not a photo (JPG, PNG, WebP) or a PDF. On an iPhone, Settings > Camera > Formats > Most Compatible takes JPG photos.", file.name);
                continue;
            }
            if (file.size > maxMb * 1024 * 1024) {
                this.photo.error = _t("%(name)s is larger than %(mb)s MB.", { name: file.name, mb: maxMb });
                continue;
            }
            const data = await readAsBase64(file);
            if (!data) {
                this.photo.error = _t("%s could not be read.", file.name);
                continue;
            }
            ready.push({ key: ++pageKey, name: file.name, size: file.size, mimetype: file.type, data,
                         url: URL.createObjectURL(file), local: true });
        }
        if (!ready.length) {
            return;
        }
        if (!(await this.confirmPhotoFill())) {
            ready.forEach((file) => URL.revokeObjectURL(file.url));
            return;
        }
        await this.readPhoto(ready);
    },

    /** What was typed is replaced by what the photo says: asked once. */
    async confirmPhotoFill() {
        if (this.photo.read || (!this.items.length && !this.state.supplier && !this.state.ref.trim())) {
            return true;
        }
        return new Promise((resolve) => {
            this.dialogService.add(ConfirmationDialog, {
                title: _t("Fill in from the photo?"),
                body: _t("The customer, number, dates and lines typed here are replaced by what the photo says."),
                confirmLabel: _t("Fill in"), cancelLabel: _t("Keep what is typed"),
                confirm: () => resolve(true), cancel: () => resolve(false),
            }, { onClose: () => resolve(false) });
        });
    },

    /**
     * One request reads the photos; a second, when the rules leave lines
     * between close items, has those chosen. Each is its own request, as
     * Odoo stops a request after two minutes. Stop leaves the window as it
     * was.
     */
    async readPhoto(newFiles = [], { again = false } = {}) {
        if (this.photo.busy) {
            return;
        }
        const seq = ++this.photoSeq;
        if (again) {
            this.photo.attempt += 1;
        }
        const count = this.photo.files.length + newFiles.length;
        this.photo.busy = count > 1 ? _t("Reading the %s photos…", count) : _t("Reading the invoice…");
        Object.assign(this.photo, { error: "", note: "", unsent: newFiles });
        const kwargs = { warehouse_id: this.props.warehouseId || false };
        try {
            const result = await this.orm.call("odin.stock.book", "book_sale_photo_read",
                [newFiles.map((file) => ({ name: file.name, data: file.data }))],
                { ...kwargs, photo_id: this.photo.id || false, attempt: this.photo.attempt });
            if (seq !== this.photoSeq) {
                return;
            }
            newFiles.forEach((file) => URL.revokeObjectURL(file.url));
            this.photo.unsent = [];
            this.photo.id = result.photo.id;
            this.photo.files = result.photo.files.map((file) => ({ ...file, key: ++pageKey }));
            let prefill = result.prefill;
            if (prefill.pending?.length) {
                this.photo.busy = _t("Choosing between similar items for %s line(s)…", prefill.pending.length);
                try {
                    prefill = (await this.orm.call("odin.stock.book", "book_sale_photo_choose", [this.photo.id], kwargs)).prefill;
                } catch (error) {
                    if (!shownError(error)) {
                        throw error;
                    }
                    // The lines stay as the rules left them: highlighted, to check.
                    this.photo.note = error.data.message;
                }
                if (seq !== this.photoSeq) {
                    return;
                }
            }
            await this.applyPhoto(prefill);
        } catch (error) {
            if (seq !== this.photoSeq) {
                return;
            }
            if (!shownError(error)) {
                throw error;
            }
            this.photo.error = error.data.message;
        } finally {
            if (seq === this.photoSeq) {
                this.photo.busy = "";
            }
        }
    },

    stopPhoto() {
        this.photoSeq++;
        this.photo.busy = "";
    },

    async removePhoto(file) {
        if (file.local) {
            URL.revokeObjectURL(file.url);
            this.photo.unsent = this.photo.unsent.filter((other) => other.key !== file.key);
            return;
        }
        const result = await this.orm.call("odin.stock.book", "book_sale_photo_remove", [this.photo.id, file.id]);
        this.photo.files = result.files.map((other) => ({ ...other, key: ++pageKey }));
        if (this.photo.read && this.photo.files.length) {
            this.photo.note = _t("A page was taken off: Read again to read the others without it.");
        }
    },

    get photoPages() {
        return [...this.photo.files, ...this.photo.unsent];
    },

    thumbnail(file) {
        if (file.local) {
            return file.mimetype === "application/pdf" ? "" : file.url;
        }
        return file.mimetype === "application/pdf" ? "" : `/web/image/${file.id}/120x120`;
    },

    // ------------------------------------------------------------ filling in
    async applyPhoto(prefill) {
        const state = this.state;
        Object.assign(state, { tried: false, error: "", refProblem: "" });
        state.supplier = prefill.supplier || null;
        state.supplierQuery = prefill.supplier ? prefill.supplier.name : prefill.supplier_query || "";
        if (prefill.purchase_ref) {
            state.ref = prefill.purchase_ref;
        }
        if (prefill.date) {
            state.date = prefill.date;
        }
        state.pin = prefill.partner_vat || "";
        const terms = prefill.payment_term_id || prefill.supplier?.payment_term_id;
        state.terms = terms ? String(terms) : "";
        state.cu = prefill.cu_invoice_no || "";
        state.lines = prefill.lines.map((row) => ({
            ...this.newLine(), product: row.product || null, query: row.product ? row.product.display_name : row.query,
            note: row.note || "", qty: row.qty === null || row.qty === undefined ? "" : String(row.qty),
            cost: row.cost === null || row.cost === undefined ? "" : String(row.cost),
            taxId: row.tax_id || false, taxTouched: Boolean(row.tax_id),
            // The invoice's rate stays: the pricelist's is shown beside it, not put in.
            keepPrice: true, priced: row.photo.price ?? null, pricelist: row.photo.pricelist || "",
            photo: { ...row.photo, ok: false },
        }));
        if (!state.lines.length) {
            state.lines.push(this.newLine());
        }
        this.ensureBlankLine();
        Object.assign(this.photo, {
            read: true, flags: prefill.flags || [], total: prefill.total ?? null,
            choices: prefill.supplier ? [] : prefill.customer_choices || [],
            header: { pin: prefill.partner_vat || "", terms: prefill.payment_term_id ? String(prefill.payment_term_id) : "" },
        });
        await this.loadDue();
        await Promise.all(state.lines.filter((line) => line.product).map((line) => this.loadDefaults(line)));
        if (state.ref && state.supplier?.id && !state.replaceIds.length) {
            await this.checkRef();
        }
        await this.loadTotals();
        this.focus(this.photoFirstCell);
    },

    /** The customer, when the photo left a choice: picked, with the invoice's terms and PIN. */
    pickPhotoCustomer(choice) {
        this.pickSupplier(choice);
        const header = this.photo.header || {};
        if (header.terms) {
            this.state.terms = header.terms;
            this.loadDue();
        }
        if (header.pin && !this.state.pin) {
            this.state.pin = header.pin;
        }
        this.photo.choices = [];
    },

    get photoFirstCell() {
        if (!this.state.supplier) {
            return "supplier";
        }
        const line = this.items.find((other) => this.photoToCheck(other));
        return line ? `photo-${line.key}` : this.firstCell;
    },

    // ------------------------------------------------------------ checking
    /** A line the photo or the catalogue left in doubt, not yet looked at. */
    photoToCheck(line) {
        return Boolean(line.photo && line.photo.level === "check" && !line.photo.ok && !this.isBlank(line));
    },

    photoOk(line) {
        line.photo.ok = true;
        const next = this.items.find((other) => this.photoToCheck(other));
        this.focus(next ? `photo-${next.key}` : this.firstCell);
    },

    /** Picking the item, or typing a number, is looking at the line. */
    pickItem(line, item) {
        super.pickItem(line, item);
        if (line.photo && line.product === item) {
            line.photo.ok = true;
        }
    },

    onNumber(line, field, ev) {
        super.onNumber(line, field, ev);
        if (line.photo) {
            line.photo.ok = true;
        }
    },

    onTax(line, ev) {
        super.onTax(line, ev);
        if (line.photo) {
            line.photo.ok = true;
        }
    },

    get problem() {
        const problem = super.problem;
        if (!this.isSale || (problem && problem[0] !== "paid")) {
            return problem;
        }
        const index = this.items.findIndex((line) => this.photoToCheck(line));
        if (index < 0) {
            return problem;
        }
        return [`photo-${this.items[index].key}`, _t("Line %s: compare it with the photo, then press Looks right, or correct it.", index + 1)];
    },

    photoRowClass(line) {
        if (!line.photo) {
            return "";
        }
        if (this.photoToCheck(line)) {
            return "o_sb_ph_check";
        }
        return line.photo.ok ? "o_sb_ph_seen" : `o_sb_ph_${line.photo.level}`;
    },

    /** What the invoice says for the line, to compare: 6 × 285.00 = 1,710.00 · 16.0% S. */
    photoNumbers(line) {
        const { qty, rate, amount, vat } = line.photo;
        const parts = [];
        if (qty !== null || rate !== null) {
            const times = `${qty ?? "?"} × ${rate === null ? "?" : this.money(rate)}`;
            parts.push(amount === null ? times : times + " = " + this.money(amount));
        }
        if (vat) {
            parts.push(vat);
        }
        return parts.join(" · ");
    },

    photoWarnings(line) {
        return line.photo.flags.filter((flag) => flag.level === "warn");
    },

    photoNotes(line) {
        return line.photo.flags.filter((flag) => flag.level !== "warn").map((flag) => flag.text).join("\n");
    },

    get photoSummary() {
        const lines = this.items.filter((line) => line.photo);
        if (!lines.length) {
            return "";
        }
        const left = lines.filter((line) => this.photoToCheck(line)).length;
        return left
            ? _t("Filled in from the photo: %(n)s lines, %(left)s highlighted to check. Compare every line with the paper before saving.", { n: lines.length, left })
            : _t("Filled in from the photo: %s lines. Compare them with the paper before saving.", lines.length);
    },

    /** The window's own total against the invoice's, once every line is complete. */
    get photoTotalNote() {
        const printed = this.photo?.total;
        const totals = this.state.totals;
        if (!this.photo?.read || printed === null || printed === undefined || !totals
            || this.items.some((line) => this.lineProblem(line))) {
            return null;
        }
        if (Math.abs(totals.total - printed) < 0.005) {
            return { level: "ok", text: _t("The same total as the invoice: %s.", this.money(printed)) };
        }
        return { level: "warn", text: _t("The window comes to %(window)s; the invoice prints %(invoice)s.",
                                         { window: this.money(totals.total), invoice: this.money(printed) }) };
    },

    savePayload() {
        const payload = super.savePayload();
        if (this.isSale && this.photo.id) {
            payload.photo_ids = [this.photo.id];
        }
        return payload;
    },
});
