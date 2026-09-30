import { Component, useState } from "@odoo/owl";
import { feedback, newUuid } from "./desk_model";
import { ItemsEditor } from "./items_editor";

/** Spirits move by the bottle, beer and sodas by the crate. */
export function packUnit(product) {
    if (product.poured) {
        return product.bottles[0].id;
    }
    return product.packs[0]?.id || product.uom.id;
}

/**
 * Log a move from the paper sheet: from, to, the items, Save. Posted at once.
 *
 * "When" places the move: on the day being closed (the default while its
 * counts are open, so they expect it) or on today. Opened from a missed-move
 * suggestion, everything comes filled in.
 */
export class MoveScreen extends Component {
    static template = "odin_bar_desk.MoveScreen";
    static components = { ItemsEditor };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.packUnit = packUnit;
        const params = this.props.params || {};
        const home = this.desk.home;
        const sheetOpen = !home.approved && !home.day.is_today;
        this.state = useState({
            fromId: params.fromId || null,
            toId: params.toId || null,
            destinationId: null,
            member: "",
            note: "",
            onSheetDay: params.onSheetDay ?? sheetOpen,
            items: (params.items || []).map((item) => ({ key: newUuid(), ...item })),
            busy: false,
            error: "",
        });
    }

    get home() {
        return this.desk.home;
    }

    get locations() {
        return this.desk.catalog?.locations || [];
    }

    get destinations() {
        return (this.desk.catalog?.destinations || []).filter((destination) => !destination.setup_issue);
    }

    get destination() {
        return this.destinations.find((destination) => destination.id === this.state.destinationId);
    }

    get sheetDayOpen() {
        return !this.home.approved && !this.home.day.is_today;
    }

    get canSave() {
        const { fromId, toId, destinationId, items, member, busy } = this.state;
        if (busy || !fromId || !items.length || (!toId && !destinationId)) {
            return false;
        }
        return !this.destination?.require_member || Boolean(member.trim());
    }

    pickFrom(location) {
        this.state.fromId = location.id;
        if (this.state.toId === location.id) {
            this.state.toId = null;
        }
    }

    pickTo(location) {
        Object.assign(this.state, { toId: location.id, destinationId: null });
    }

    pickDestination(destination) {
        Object.assign(this.state, { toId: null, destinationId: destination.id });
    }

    label(locationId) {
        return this.model.location(locationId)?.name || "";
    }

    async save() {
        if (!this.canSave) {
            return;
        }
        const { fromId, toId, destinationId, items, member, note, onSheetDay } = this.state;
        const to = toId ? this.label(toId) : this.destination.name;
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_move",
            {
                from_bar_id: fromId,
                to_bar_id: toId || false,
                reason_id: destinationId || false,
                business_date: onSheetDay && this.sheetDayOpen ? this.home.day.date : false,
                member_ref: member.trim() || false,
                note: note.trim() || false,
                lines: items.map((item) => ({ product_id: item.productId, uom_id: item.uomId, qty: item.qty })),
            },
            `Move ${this.label(fromId)} → ${to}`
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
                ? "No connection: the move is kept on this device and sent automatically."
                : `Moved from ${this.label(fromId)} to ${to}.`,
            outcome.status === "queued" ? "warning" : "success"
        );
        this.props.app.back();
    }
}
