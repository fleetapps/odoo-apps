import { Component, onWillStart, useState } from "@odoo/owl";
import { errorMessage, feedback } from "./desk_model";
import { ItemsEditor } from "./items_editor";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { fmt, moveQty } from "./utils";

export const REQUEST_STATES = {
    open: "Waiting",
    sent: "Sent",
    partial: "Part sent",
    none: "Not available",
    cancel: "Cancelled",
};

/** Beer by the crate, spirits by the bottle. */
function packUnit(product) {
    if (product.poured) {
        return product.bottles[0].id;
    }
    return product.packs[0]?.id || product.uom.id;
}

function done(app, outcome, message) {
    feedback(outcome.status !== "failed");
    app.toast(
        outcome.status === "queued" ? "No connection: saved on this device and sent automatically." : message,
        outcome.status === "queued" ? "warning" : "success"
    );
}

/** A bar asks the store or another bar for stock, and follows its asks. */
export class AskScreen extends Component {
    static template = "odin_bar_desk.AskScreen";
    static components = { ItemsEditor };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.packUnit = packUnit;
        this.moveQty = moveQty;
        this.fmt = fmt;
        this.labels = REQUEST_STATES;
        this.state = useState({ loading: true, data: null, source: null, items: [], busy: false, error: "" });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            this.state.data = await this.model.fetch("desk_requests");
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    missing(request) {
        return request.lines.filter((line) => line.missing_qty > 0);
    }

    async ask() {
        const { source, items } = this.state;
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_request_create",
            {
                source_bar_id: source.id,
                lines: items.map((item) => ({ product_id: item.productId, uom_id: item.uomId, qty: item.qty })),
            },
            `Ask ${source.name}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        done(this.props.app, outcome, `Asked ${source.name}. It shows in Stock in once sent.`);
        Object.assign(this.state, { source: null, items: [] });
        await this.load();
    }

    async passOn(request, source) {
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_request_pass_on",
            { request_id: request.id, source_bar_id: source.id },
            `Ask ${source.name}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        done(this.props.app, outcome, `Asked ${source.name}.`);
        await this.load();
    }

    async cancel(request) {
        try {
            await this.model.fetch("desk_request_cancel", { request_id: request.id });
            await this.load();
        } catch (error) {
            this.state.error = errorMessage(error);
        }
    }
}

/** Asks waiting for this location: open one to send what you have. */
export class RequestsScreen extends Component {
    static template = "odin_bar_desk.RequestsScreen";
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.state = useState({ loading: true, requests: [], error: "" });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            this.state.requests = (await this.model.fetch("desk_requests")).incoming;
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    open(request) {
        this.props.app.go("request", { request });
    }
}

/** One ask. What was asked is filled in: change what differs, 0 when you
 * don't have it, then Send. */
export class RequestScreen extends Component {
    static template = "odin_bar_desk.RequestScreen";
    static components = { Keypad, LineList };
    static props = { app: Object, params: Object };

    setup() {
        this.model = this.props.app.model;
        this.moveQty = moveQty;
        this.request = this.props.params.request;
        this.state = useState({
            sent: Object.fromEntries(this.request.lines.map((line) => [line.id, line.qty])),
            keypad: null,
            busy: false,
            error: "",
        });
    }

    get lines() {
        return this.request.lines.map((line) => {
            const sent = this.state.sent[line.id];
            const changed = Math.abs(sent - line.qty) > 1e-6;
            return {
                key: String(line.id),
                line,
                name: line.name,
                hint: !sent ? "Not available" : changed ? `Asked ${moveQty(line.qty, line)}` : "",
                label: moveQty(sent, line),
                state: !sent ? "error" : changed ? "changed" : "entered",
            };
        });
    }

    get sendingNothing() {
        return this.request.lines.every((line) => !this.state.sent[line.id]);
    }

    tap(displayLine) {
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
            value: { qty: this.state.sent[line.id], uomId: line.uom_id },
        };
    }

    bump(displayLine, delta) {
        const id = displayLine.line.id;
        this.state.sent[id] = Math.max(0, this.state.sent[id] + delta);
    }

    confirm({ qty }) {
        this.state.sent[this.state.keypad.line.id] = qty;
        this.state.keypad = null;
    }

    async send() {
        const bar = this.request.bar;
        if (this.sendingNothing) {
            const confirmed = await this.props.app.confirm(
                "None of it available?",
                `${bar} will be told you don't have these, so they can ask elsewhere.`,
                "Don't have any"
            );
            if (!confirmed) {
                return;
            }
        }
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_request_answer",
            { request_id: this.request.id, sent: this.state.sent },
            `Answer ${bar}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        done(this.props.app, outcome, this.sendingNothing ? `${bar} told you don't have it.` : `Sent to ${bar}.`);
        this.props.app.back();
    }
}
