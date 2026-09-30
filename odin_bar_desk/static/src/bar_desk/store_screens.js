import { Component, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { errorMessage, feedback } from "./desk_model";
import { ItemsEditor } from "./items_editor";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { moveQty } from "./utils";

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

/** Store: pick the bar, add items, Send. Validated at once. */
export class StoreSendScreen extends Component {
    static template = "odin_bar_desk.StoreSendScreen";
    static components = { ItemsEditor };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.packUnit = packUnit;
        this.state = useState({ bar: null, items: [], busy: false, error: "" });
    }

    get bars() {
        return this.desk.catalog?.bars || [];
    }

    async send() {
        const { bar, items } = this.state;
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_store_send",
            {
                dest_bar_id: bar.id,
                lines: items.map((item) => ({ product_id: item.productId, uom_id: item.uomId, qty: item.qty })),
            },
            `Send to ${bar.name}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        toast(this.props.app, outcome, `Sent to ${bar.name}.`);
        this.props.app.home();
    }
}

/** Store: book what a supplier delivered, or a receipt waiting from a purchase order. */
export class StoreReceiveScreen extends Component {
    static template = "odin_bar_desk.StoreReceiveScreen";
    static components = { ItemsEditor, Keypad, LineList };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.packUnit = packUnit;
        this.moveQty = moveQty;
        this.state = useState({
            step: "choose",
            receipts: [],
            query: "",
            suppliers: [],
            supplier: null,
            items: [],
            receipt: null,
            received: {},
            keypad: null,
            busy: false,
            error: "",
        });
        onWillStart(() => this.loadReceipts());
        this.searchTimer = null;
    }

    async loadReceipts() {
        try {
            this.state.receipts = await this.model.fetch("desk_receipts");
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    async newReceipt() {
        this.state.step = "supplier";
        await this.searchSuppliers();
    }

    onQuery() {
        browser.clearTimeout(this.searchTimer);
        this.searchTimer = browser.setTimeout(() => this.searchSuppliers(), 300);
    }

    async searchSuppliers() {
        try {
            this.state.suppliers = await this.model.fetch("desk_suppliers", { query: this.state.query });
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }

    pickSupplier(supplier) {
        Object.assign(this.state, { supplier, step: "items" });
    }

    async openReceipt(receipt) {
        try {
            const detail = await this.model.fetch("desk_receipt", { picking_id: receipt.id });
            Object.assign(this.state, {
                receipt: detail,
                received: Object.fromEntries(detail.lines.map((line) => [line.move_id, line.qty])),
                step: "receipt",
            });
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
                hint: changed ? `Ordered ${moveQty(line.qty, line)}` : "",
                label: moveQty(received, line),
                state: changed ? "changed" : "untouched",
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
        };
    }

    bumpReceiptLine(displayLine, delta) {
        const id = displayLine.line.move_id;
        this.state.received[id] = Math.max(0, this.state.received[id] + delta);
    }

    confirmReceiptLine({ qty }) {
        this.state.received[this.state.keypad.line.move_id] = qty;
        this.state.keypad = null;
    }

    async finish() {
        const { receipt, supplier, items, received } = this.state;
        this.state.busy = true;
        this.state.error = "";
        let outcome;
        if (receipt) {
            outcome = await this.model.post(
                "desk_receipt_validate",
                { picking_id: receipt.id, received },
                `Receive ${receipt.name}`
            );
        } else {
            outcome = await this.model.post(
                "desk_store_receive",
                {
                    partner_id: supplier.id,
                    lines: items.map((item) => ({ product_id: item.productId, uom_id: item.uomId, qty: item.qty })),
                },
                `Receive from ${supplier.name}`
            );
        }
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        toast(this.props.app, outcome, "Received into the store.");
        this.props.app.home();
    }
}

/** Store: bars' disputes on deliveries. Accept when the goods never left. */
export class DisputesScreen extends Component {
    static template = "odin_bar_desk.DisputesScreen";
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.moveQty = moveQty;
        this.state = useState({ loading: true, disputes: [], busy: false, error: "" });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            this.state.disputes = await this.model.fetch("desk_disputes");
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    async resolve(dispute, accept) {
        const confirmed = await this.props.app.confirm(
            accept ? "Accept the dispute?" : "Reject the dispute?",
            accept
                ? dispute.short
                    ? "The goods never left the store: they come back into store stock."
                    : "The extra goods were sent: they are added to the delivery."
                : "Nothing changes: the difference stays with the bar.",
            accept ? "Accept" : "Reject"
        );
        if (!confirmed) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_dispute_resolve",
            { picking_id: dispute.id, accept },
            `${accept ? "Accept" : "Reject"} ${dispute.name}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        toast(this.props.app, outcome, accept ? "Dispute accepted." : "Dispute rejected.");
        await this.load();
    }
}
