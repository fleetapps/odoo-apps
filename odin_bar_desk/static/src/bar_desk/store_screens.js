import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { getDataURLFromFile } from "@web/core/utils/urls";
import { errorMessage, feedback } from "./desk_model";
import { ItemsEditor } from "./items_editor";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { fmt, moveQty, qtyLabel } from "./utils";

/** Beer and sodas go out by the crate, spirits by the bottle. */
function packUnit(product) {
    if (product.poured) {
        return product.bottles[0].id;
    }
    return product.packs[0]?.id || product.uom.id;
}

function toast(app, outcome, message) {
    feedback(outcome.status !== "failed");
    if (outcome.status !== "failed") {
        app.toast(
            outcome.status === "queued" ? "No connection: saved on this device and sent automatically." : message,
            outcome.status === "queued" ? "warning" : "success"
        );
    }
}

/**
 * Store: a supplier delivery, checked against the supplier's invoice.
 *
 * New delivery: invoice photo (optional) → supplier → the items on the
 * invoice → what arrived (filled in from the invoice: change what differs)
 * → missing items, invoice number and total, Paid now or Pay later.
 * Expected deliveries (the rest of an invoice, or an order made in Odoo)
 * are opened from the list and checked the same way.
 */
export class StoreReceiveScreen extends Component {
    static template = "odin_bar_desk.StoreReceiveScreen";
    static components = { ItemsEditor, Keypad, LineList };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        // Supplier deliveries always go into the store, whichever location the Desk signed in at.
        this.storeId = this.model.state.home.store_id;
        this.packUnit = packUnit;
        this.moveQty = moveQty;
        this.fmt = fmt;
        this.fileInput = useRef("invoiceFile");
        this.state = useState({
            step: "choose",
            receipts: [],
            query: "",
            suppliers: [],
            supplier: null,
            supplierProducts: new Set(),
            invoice: null,
            items: [],
            arrived: {},
            receipt: null,
            received: {},
            keypad: null,
            missing: null,
            rest: null,
            supplierRef: "",
            total: "",
            busy: false,
            error: "",
        });
        onWillStart(() => this.loadReceipts());
        this.searchTimer = null;
    }

    get paymentAccount() {
        return this.desk.home?.payment_account || "";
    }

    async loadReceipts() {
        try {
            this.state.receipts = await this.model.fetch("desk_receipts", { bar_id: this.storeId });
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    go(step) {
        this.state.error = "";
        this.state.step = step;
    }

    // ---- new delivery: supplier ---------------------------------------------

    async newDelivery() {
        this.go("supplier");
        await this.searchSuppliers();
    }

    onQuery() {
        browser.clearTimeout(this.searchTimer);
        this.searchTimer = browser.setTimeout(() => this.searchSuppliers(), 300);
    }

    async searchSuppliers() {
        try {
            this.state.suppliers = await this.model.fetch("desk_suppliers", { bar_id: this.storeId, query: this.state.query });
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    async pickSupplier(supplier) {
        Object.assign(this.state, { supplier, items: [], supplierProducts: new Set() });
        this.go("items");
        try {
            const ids = await this.model.fetch("desk_supplier_products", { bar_id: this.storeId, partner_id: supplier.id });
            this.state.supplierProducts = new Set(ids);
        } catch {
            // Offline: every product is offered.
        }
    }

    // ---- invoice photo --------------------------------------------------------

    chooseInvoice() {
        this.fileInput.el.click();
    }

    async onInvoiceFile(ev) {
        const file = ev.target.files[0];
        ev.target.value = null;
        if (!file) {
            return;
        }
        try {
            this.state.invoice = await readInvoice(file);
        } catch {
            this.state.error = "That file could not be read. Take a photo or pick a PDF.";
        }
    }

    removeInvoice() {
        this.state.invoice = null;
    }

    // ---- what arrived -----------------------------------------------------------

    toCheck() {
        const arrived = {};
        for (const item of this.state.items) {
            arrived[item.key] = this.state.arrived[item.key] ?? item.qty;
        }
        this.state.arrived = arrived;
        this.go("check");
    }

    get checkLines() {
        return this.state.items.map((item) => {
            const product = this.model.product(item.productId);
            const arrived = this.state.arrived[item.key];
            const changed = Math.abs(arrived - item.qty) > 1e-6;
            return {
                key: item.key,
                item,
                name: product.name,
                hint: changed ? `Invoice: ${qtyLabel(product, item.qty, item.uomId)}` : "",
                label: qtyLabel(product, arrived, item.uomId),
                state: changed ? "changed" : "entered",
            };
        });
    }

    tapCheckLine(line) {
        const product = this.model.product(line.item.productId);
        this.state.keypad = {
            key: line.item.key,
            product,
            value: { qty: this.state.arrived[line.item.key], uomId: line.item.uomId },
            hint: `Invoice: ${qtyLabel(product, line.item.qty, line.item.uomId)}`,
        };
    }

    bumpCheckLine(line, delta) {
        const key = line.item.key;
        this.state.arrived[key] = Math.max(0, this.state.arrived[key] + delta);
    }

    confirmKeypad({ qty }) {
        const keypad = this.state.keypad;
        if (keypad.key) {
            this.state.arrived[keypad.key] = qty;
        } else {
            this.state.received[keypad.line.move_id] = qty;
        }
        this.state.keypad = null;
    }

    get missingItems() {
        if (this.state.receipt) {
            return this.state.receipt.lines
                .filter((line) => this.state.received[line.move_id] < line.qty - 1e-6)
                .map((line) => ({
                    key: String(line.move_id),
                    name: line.name,
                    label: `${fmt(this.state.received[line.move_id])} of ${moveQty(line.qty, line)}`,
                }));
        }
        return this.state.items
            .filter((item) => this.state.arrived[item.key] < item.qty - 1e-6)
            .map((item) => {
                const product = this.model.product(item.productId);
                return {
                    key: item.key,
                    name: product.name,
                    label: `${fmt(this.state.arrived[item.key])} of ${qtyLabel(product, item.qty, item.uomId)}`,
                };
            });
    }

    toPay() {
        this.state.missing = this.missingItems.length ? null : "none";
        this.go("pay");
    }

    // ---- expected deliveries ------------------------------------------------------

    async openReceipt(receipt) {
        try {
            const detail = await this.model.fetch("desk_receipt", { bar_id: this.storeId, picking_id: receipt.id });
            Object.assign(this.state, {
                receipt: { ...detail, billed: receipt.billed },
                received: Object.fromEntries(detail.lines.map((line) => [line.move_id, line.qty])),
            });
            this.go("receipt");
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    get receiptLines() {
        return this.state.receipt.lines.map((line) => {
            const received = this.state.received[line.move_id];
            const changed = Math.abs(received - line.qty) > 1e-6;
            return {
                key: String(line.move_id),
                line,
                name: line.name,
                hint: changed ? `Expected ${moveQty(line.qty, line)}` : "",
                label: moveQty(received, line),
                state: changed ? "changed" : "entered",
            };
        });
    }

    tapReceiptLine(displayLine) {
        const line = displayLine.line;
        this.state.keypad = {
            line,
            product: {
                id: line.product_id,
                name: line.name,
                uom: { id: line.uom_id, name: line.uom_name, factor: 1 },
                bottles: [],
                packs: [],
                poured: false,
            },
            value: { qty: this.state.received[line.move_id], uomId: line.uom_id },
            hint: `Expected: ${moveQty(line.qty, line)}`,
        };
    }

    bumpReceiptLine(displayLine, delta) {
        const id = displayLine.line.move_id;
        this.state.received[id] = Math.max(0, this.state.received[id] + delta);
    }

    // ---- finish -----------------------------------------------------------------

    backFromPay() {
        this.go(this.state.receipt ? "receipt" : "check");
    }

    get invoiceTotal() {
        const value = parseFloat(String(this.state.total).replace(/,/g, ""));
        return Number.isFinite(value) && value > 0 ? value : 0;
    }

    get canFinish() {
        return !this.state.busy && this.state.missing !== null;
    }

    async finish(paid) {
        const { receipt, supplier, items, arrived, received, missing } = this.state;
        this.state.busy = true;
        this.state.error = "";
        let outcome;
        if (receipt) {
            outcome = await this.model.post(
                "desk_receipt_validate",
                {
                    bar_id: this.storeId,
                    picking_id: receipt.id,
                    received,
                    rest: missing === "credit" ? "not_coming" : "coming",
                    paid: receipt.billed ? false : paid,
                    supplier_ref: this.state.supplierRef.trim() || false,
                },
                `Supplier delivery ${receipt.name}`
            );
        } else {
            outcome = await this.model.post(
                "desk_supplier_delivery",
                {
                    bar_id: this.storeId,
                    partner_id: supplier.id,
                    lines: items.map((item) => ({
                        product_id: item.productId,
                        uom_id: item.uomId,
                        invoiced: item.qty,
                        received: arrived[item.key],
                    })),
                    missing: missing === "credit" ? "credit" : "coming",
                    paid,
                    amount: this.invoiceTotal || false,
                    supplier_ref: this.state.supplierRef.trim() || false,
                    invoice: this.state.invoice
                        ? { name: this.state.invoice.name, mimetype: this.state.invoice.mimetype, data: this.state.invoice.data }
                        : false,
                },
                `Supplier delivery from ${supplier.name}`
            );
        }
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        toast(
            this.props.app,
            outcome,
            paid === "now" ? "In the store and paid." : "In the store. The bill waits for payment."
        );
        this.props.app.home();
    }
}
