import { Component, onWillStart, useState } from "@odoo/owl";
import { errorMessage, feedback } from "./desk_model";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { moveQty } from "./utils";

export const ACK_LABELS = {
    not_checked: "Not checked",
    confirmed: "Confirmed",
    disputed: "Disputed",
};

const DISPUTE_LABELS = {
    pending: "waiting for the store",
    accepted: "accepted",
    rejected: "rejected",
};

/** Deliveries into this bar over the last two weeks. */
export class DeliveriesScreen extends Component {
    static template = "odin_bar_desk.DeliveriesScreen";
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.labels = ACK_LABELS;
        this.state = useState({ loading: true, deliveries: [], error: "" });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            this.state.deliveries = await this.model.fetch("desk_deliveries");
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    open(delivery) {
        this.props.app.go("delivery", { pickingId: delivery.id });
    }
}

/**
 * One delivery. Sent quantities are filled in: "All correct" is one tap.
 * Otherwise correct the lines that differ and report the difference.
 */
export class DeliveryScreen extends Component {
    static template = "odin_bar_desk.DeliveryScreen";
    static components = { Keypad, LineList };
    static props = { app: Object, params: Object };

    setup() {
        this.model = this.props.app.model;
        this.labels = ACK_LABELS;
        this.disputeLabels = DISPUTE_LABELS;
        this.moveQty = moveQty;
        this.state = useState({
            loading: true,
            delivery: null,
            received: {},
            keypad: null,
            busy: false,
            error: "",
        });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            const delivery = await this.model.fetch("desk_delivery", {
                picking_id: this.props.params.pickingId,
            });
            this.state.delivery = delivery;
            this.state.received = Object.fromEntries(delivery.lines.map((line) => [line.move_id, line.qty]));
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    get editable() {
        return this.state.delivery?.state === "not_checked";
    }

    isChanged(line) {
        return Math.abs((this.state.received[line.move_id] ?? line.qty) - line.qty) > 1e-6;
    }

    get changedLines() {
        return (this.state.delivery?.lines || []).filter((line) => this.isChanged(line));
    }

    get lines() {
        const delivery = this.state.delivery;
        return delivery.lines.map((line) => {
            const received = this.state.received[line.move_id];
            const changed = this.isChanged(line);
            let state = "untouched";
            if (!this.editable) {
                state = delivery.state === "confirmed" ? "entered" : "untouched";
            } else if (changed) {
                state = "changed";
            }
            return {
                key: String(line.move_id),
                line,
                name: line.name,
                hint: changed ? `Sent ${moveQty(line.qty, line)}` : "",
                label: moveQty(received, line),
                state,
            };
        });
    }

    keypadProduct(line) {
        return {
            id: line.product_id,
            name: line.name,
            uom: { id: line.uom_id, name: line.uom_name, factor: 1 },
            bottles: [],
            packs: [],
            poured: false,
        };
    }

    tap(displayLine) {
        if (!this.editable) {
            return;
        }
        const line = displayLine.line;
        this.state.keypad = {
            line,
            product: this.keypadProduct(line),
            value: { qty: this.state.received[line.move_id], uomId: line.uom_id },
        };
    }

    bump(displayLine, delta) {
        if (this.editable) {
            const id = displayLine.line.move_id;
            this.state.received[id] = Math.max(0, this.state.received[id] + delta);
        }
    }

    confirm({ qty }) {
        this.state.received[this.state.keypad.line.move_id] = qty;
        this.state.keypad = null;
    }

    reset() {
        for (const line of this.state.delivery.lines) {
            this.state.received[line.move_id] = line.qty;
        }
    }

    async check() {
        const delivery = this.state.delivery;
        const changed = this.changedLines;
        if (changed.length) {
            const confirmed = await this.props.app.confirm(
                "Report a difference?",
                "The store is asked to confirm what was really sent. Until then the stock stays as sent.",
                "Report"
            );
            if (!confirmed) {
                return;
            }
        }
        this.state.busy = true;
        this.state.error = "";
        const received = Object.fromEntries(changed.map((line) => [line.move_id, this.state.received[line.move_id]]));
        const outcome = await this.model.post(
            "desk_delivery_check",
            { picking_id: delivery.id, received },
            `${changed.length ? "Dispute" : "Check"} ${delivery.name}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        feedback(true);
        const queued = outcome.status === "queued";
        this.props.app.toast(
            queued
                ? "No connection: saved on this device and sent automatically."
                : changed.length
                  ? "Difference reported to the store."
                  : "Delivery confirmed.",
            queued ? "warning" : "success"
        );
        this.props.app.back();
    }
}
