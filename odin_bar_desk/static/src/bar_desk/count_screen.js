import { Component, onWillUnmount, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { errorMessage, feedback, isRetryable } from "./desk_model";
import { Keypad } from "./keypad";
import { LineList } from "./line_list";
import { ProductPicker } from "./product_picker";
import { bumpCountLine, countLabel, emptyCountLine, normalize } from "./utils";

const SAVE_DELAY = 1200;
const RETRY_DELAY = 20000;

const STATE_NOTES = {
    draft: "In progress: you carry on where it was left.",
    submitted: "Already submitted. Counting again replaces it.",
    recount: "A recount was requested.",
    approved: "Approved. It cannot be counted again.",
};

/**
 * Blind count. A closing count lists the whole count sheet in paper order and
 * every line must be entered, zeros included. A spot count takes a few items.
 * Lines save as you go, on the server and on this device.
 */
export class CountScreen extends Component {
    static template = "odin_bar_desk.CountScreen";
    static components = { Keypad, LineList, ProductPicker };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.state = useState({
            phase: "choose",
            otherDays: false,
            count: null,
            lines: {},
            order: [],
            filter: "all",
            query: "",
            keypad: null,
            picking: false,
            save: "saved",
            busy: false,
            error: "",
        });
        this.dirty = false;
        this.saveTimer = null;
        onWillUnmount(() => {
            browser.clearTimeout(this.saveTimer);
            if (this.dirty) {
                this.saveNow();
            }
        });
    }

    // ------------------------------------------------------------------
    // Choosing what to count
    // ------------------------------------------------------------------

    get status() {
        return this.desk.home?.count;
    }

    get closingName() {
        return this.model.isStore ? "Store count" : "Closing count";
    }

    get days() {
        return this.status?.days || [];
    }

    note(day) {
        return STATE_NOTES[day.state] || "";
    }

    async start(kind, businessDate = false, restart = false) {
        this.state.busy = true;
        this.state.error = "";
        try {
            const data = await this.model.fetch("desk_count_start", {
                kind,
                business_date: businessDate,
                restart,
            });
            this.load(data);
        } catch (error) {
            feedback(false);
            this.state.error = errorMessage(error);
        } finally {
            this.state.busy = false;
        }
    }

    async startOver() {
        const confirmed = await this.props.app.confirm(
            "Start over?",
            "Everything entered in this count is dropped.",
            "Start over"
        );
        if (confirmed) {
            const { kind, business_date, id } = this.state.count;
            this.dirty = false;
            this.model.writeCountDraft(id, undefined);
            await this.start(kind, business_date, true);
        }
    }

    load(data) {
        const lines = {};
        const order = [];
        for (const line of data.lines) {
            lines[line.product_id] = { ...emptyCountLine(), ...line };
            order.push(line.product_id);
        }
        // Changes that never reached the server win over what the server has.
        const draft = this.model.readCountDraft(data.id);
        this.dirty = Boolean(draft?.dirty);
        if (this.dirty) {
            for (const [productId, line] of Object.entries(draft.lines || {})) {
                const id = parseInt(productId);
                if (!(id in lines)) {
                    order.push(id);
                }
                lines[id] = line;
            }
        }
        Object.assign(this.state, {
            count: {
                id: data.id,
                kind: data.kind,
                business_date: data.business_date,
                day_label: data.day_label,
            },
            lines,
            order: this.sorted(order.filter((id) => this.model.product(id))),
            phase: "counting",
            filter: "all",
            query: "",
            save: this.dirty ? "pending" : "saved",
        });
        if (this.dirty) {
            this.scheduleSave(0);
        }
    }

    // ------------------------------------------------------------------
    // Counting
    // ------------------------------------------------------------------

    sorted(productIds) {
        const position = (id) => this.model.product(id)?.position ?? Number.MAX_SAFE_INTEGER;
        return [...productIds].sort((a, b) => position(a) - position(b));
    }

    get isSpot() {
        return this.state.count?.kind === "spot";
    }

    get heading() {
        const count = this.state.count;
        return this.isSpot ? `Spot count · ${count.day_label}` : `${this.closingName} for ${count.day_label}`;
    }

    get progress() {
        const total = this.state.order.length;
        const done = this.state.order.filter((id) => this.state.lines[id]?.touched).length;
        return { total, done, left: total - done, percent: total ? Math.round((100 * done) / total) : 0 };
    }

    get categoryNames() {
        return Object.fromEntries((this.desk.catalog?.categories || []).map((c) => [c.id, c.name]));
    }

    get lines() {
        const query = normalize(this.state.query.trim());
        const names = this.categoryNames;
        const result = [];
        let lastCategory = null;
        for (const id of this.state.order) {
            const product = this.model.product(id);
            const line = this.state.lines[id];
            if ((this.state.filter === "todo" && line.touched) || (query && !normalize(product.name).includes(query))) {
                continue;
            }
            const category = names[product.categ_id] || "";
            result.push({
                key: String(id),
                productId: id,
                header: category !== lastCategory ? category : false,
                name: product.name,
                label: countLabel(product, line),
                state: line.touched ? "entered" : "untouched",
            });
            lastCategory = category;
        }
        return result;
    }

    get pickedIds() {
        return new Set(this.state.order);
    }

    get canSubmit() {
        const { done, left } = this.progress;
        return !this.state.busy && (this.isSpot ? done > 0 : left === 0);
    }

    get saveLabel() {
        return {
            saved: "Saved",
            pending: "Saving…",
            saving: "Saving…",
            offline: "No connection: kept on this device",
            error: "Not saved",
        }[this.state.save];
    }

    tap(line) {
        const product = this.model.product(line.productId);
        this.state.keypad = { product, value: this.state.lines[product.id] };
    }

    bump(line, delta) {
        const product = this.model.product(line.productId);
        this.setLine(product.id, bumpCountLine(product, this.state.lines[product.id], delta));
    }

    pick(product) {
        this.state.picking = false;
        this.state.keypad = { product, value: this.state.lines[product.id] || emptyCountLine() };
    }

    confirm(value) {
        this.setLine(this.state.keypad.product.id, value);
        this.state.keypad = null;
    }

    removeCurrent() {
        // Spot counts only: take an item back off the list.
        const id = this.state.keypad.product.id;
        this.state.keypad = null;
        this.state.lines[id] = emptyCountLine();
        this.state.order = this.state.order.filter((other) => other !== id);
        this.changed();
    }

    setLine(productId, value) {
        this.state.lines[productId] = value;
        if (!this.state.order.includes(productId)) {
            this.state.order = this.sorted([...this.state.order, productId]);
        }
        this.changed();
    }

    changed() {
        this.dirty = true;
        this.persist();
        this.scheduleSave();
    }

    // ------------------------------------------------------------------
    // Saving
    // ------------------------------------------------------------------

    payload() {
        return Object.entries(this.state.lines)
            .filter(([, line]) => line.touched)
            .map(([productId, line]) => ({
                product_id: parseInt(productId),
                touched: true,
                unit_qty: line.unit_qty || 0,
                bottle_detail: line.bottle_detail || {},
                open_tots: line.open_tots || 0,
            }));
    }

    persist() {
        const lines = {};
        for (const [productId, line] of Object.entries(this.state.lines)) {
            if (line.touched) {
                lines[productId] = line;
            }
        }
        this.model.writeCountDraft(this.state.count.id, { dirty: this.dirty, lines });
    }

    scheduleSave(delay = SAVE_DELAY) {
        browser.clearTimeout(this.saveTimer);
        this.state.save = "pending";
        this.saveTimer = browser.setTimeout(() => this.saveNow(), delay);
    }

    async saveNow() {
        if (!this.dirty || !this.state.count) {
            return;
        }
        const countId = this.state.count.id;
        this.dirty = false;
        this.state.save = "saving";
        try {
            await this.model.fetch("desk_count_save", { count_id: countId, lines: this.payload() });
            if (!this.dirty) {
                this.state.save = "saved";
                this.model.writeCountDraft(countId, undefined);
            }
        } catch (error) {
            this.dirty = true;
            this.persist();
            if (isRetryable(error)) {
                this.state.save = "offline";
                this.saveTimer = browser.setTimeout(() => this.saveNow(), RETRY_DELAY);
            } else {
                this.state.save = "error";
                this.state.error = errorMessage(error);
            }
        }
    }

    async submit() {
        if (!this.canSubmit) {
            return;
        }
        const confirmed = await this.props.app.confirm(
            "Submit the count?",
            "It goes to a manager and cannot be changed after.",
            "Submit"
        );
        if (!confirmed) {
            return;
        }
        browser.clearTimeout(this.saveTimer);
        const { id, day_label } = this.state.count;
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_count_submit",
            { count_id: id, lines: this.payload() },
            `${this.isSpot ? "Spot count" : this.closingName} for ${day_label}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        feedback(true);
        this.dirty = false;
        this.model.writeCountDraft(id, undefined);
        this.props.app.toast(
            outcome.status === "queued"
                ? "No connection: the count is kept on this device and submitted automatically."
                : "Count submitted. Thank you!",
            outcome.status === "queued" ? "warning" : "success"
        );
        this.props.app.home();
    }
}
