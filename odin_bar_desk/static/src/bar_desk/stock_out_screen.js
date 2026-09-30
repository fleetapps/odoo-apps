import { Component, useState } from "@odoo/owl";
import { feedback } from "./desk_model";
import { ItemsEditor } from "./items_editor";

/**
 * Stock out: pick why it left, add the items, Done.
 * "To another bar" first asks which bar; "Unpaid bill" asks for the member.
 */
export class StockOutScreen extends Component {
    static template = "odin_bar_desk.StockOutScreen";
    static components = { ItemsEditor };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.state = useState({
            step: "reason",
            reason: null,
            destBar: null,
            member: "",
            items: [],
            busy: false,
            error: "",
        });
    }

    get reasons() {
        return this.desk.catalog?.reasons || [];
    }

    get bars() {
        return this.desk.catalog?.bars || [];
    }

    get canFinish() {
        const { reason, items, member, busy } = this.state;
        return !busy && items.length && (!reason.require_member || member.trim());
    }

    get title() {
        const { reason, destBar } = this.state;
        if (!reason) {
            return "Why is it leaving?";
        }
        return destBar ? `${reason.name}: ${destBar.name}` : reason.name;
    }

    pickReason(reason) {
        if (reason.setup_issue) {
            return;
        }
        this.state.reason = reason;
        this.state.step = reason.operation === "transfer" ? "bar" : "items";
    }

    pickBar(bar) {
        this.state.destBar = bar;
        this.state.step = "items";
    }

    back() {
        if (this.state.step === "items" && this.state.reason.operation === "transfer") {
            this.state.step = "bar";
        } else {
            Object.assign(this.state, { step: "reason", reason: null, destBar: null });
        }
    }

    defaultUnit(product) {
        if (product.poured && this.state.reason?.default_unit === "bottle") {
            return product.bottles[0].id;
        }
        return product.uom.id;
    }

    async done() {
        if (!this.canFinish) {
            return;
        }
        const { reason, destBar, member, items } = this.state;
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_stock_out",
            {
                reason_id: reason.id,
                lines: items.map((item) => ({
                    product_id: item.productId,
                    uom_id: item.uomId,
                    qty: item.qty,
                })),
                dest_bar_id: destBar?.id || false,
                member_ref: member.trim() || false,
            },
            reason.name
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        feedback(true);
        this.props.app.toast(
            outcome.status === "queued"
                ? `${reason.name}: no connection, saved on this device and sent automatically.`
                : `${reason.name} recorded.`,
            outcome.status === "queued" ? "warning" : "success"
        );
        this.props.app.home();
    }
}
