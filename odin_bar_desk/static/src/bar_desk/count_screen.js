import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { errorMessage, feedback, isRetryable } from "./desk_model";
import { Keypad } from "./keypad";
import { ProductPicker } from "./product_picker";
import {
    countedQty,
    countLabel,
    countLineFor,
    diffLabel,
    emptyCountLine,
    fmt,
    normalize,
    stockLabel,
} from "./utils";

const SAVE_DELAY = 1200;
const RETRY_DELAY = 20000;

/**
 * Closing count of one location for the day on the sheet, taken the next
 * morning. Each line shows what is expected (the last count, plus what moved
 * in, less what moved out and what the POS sold): tap Same when it matches,
 * otherwise type what is there; the difference shows at once. Lines save as
 * you go, on the server and on this device.
 */
export class CountScreen extends Component {
    static template = "odin_bar_desk.CountScreen";
    static components = { Keypad, ProductPicker };
    static props = { app: Object, params: Object };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.barId = this.props.params.barId;
        this.fmt = fmt;
        this.state = useState({
            loading: true,
            count: null,
            lines: {},
            expected: {},
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
        onWillStart(() => this.start());
        onWillUnmount(() => {
            browser.clearTimeout(this.saveTimer);
            if (this.dirty) {
                this.saveNow();
            }
        });
    }

    fetch(method, kwargs = {}) {
        return this.model.fetch(method, { bar_id: this.barId, ...kwargs });
    }

    async start(restart = false) {
        this.state.error = "";
        try {
            const data = await this.fetch("desk_count_start", {
                business_date: this.desk.home.day.date,
                restart,
            });
            this.load(data);
        } catch (error) {
            feedback(false);
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    async startOver() {
        const confirmed = await this.props.app.confirm(
            "Start over?",
            "Everything entered in this count is dropped.",
            "Start over"
        );
        if (confirmed) {
            this.dirty = false;
            this.model.writeCountDraft(this.state.count.id, undefined);
            this.state.loading = true;
            await this.start(true);
        }
    }

    load(data) {
        const lines = {};
        const expected = {};
        const order = [];
        for (const line of data.lines) {
            const { opening, moved, sold, expected: qty, ...counted } = line;
            lines[line.product_id] = { ...emptyCountLine(), ...counted };
            expected[line.product_id] = { opening, moved, sold, qty };
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
                bar: data.bar,
                business_date: data.business_date,
                day_label: data.day_label,
                pos_missing_label: data.pos_missing_label,
            },
            lines,
            expected,
            order: this.sorted(order.filter((id) => this.model.product(id))),
            filter: "all",
            query: "",
            save: this.dirty ? "pending" : "saved",
        });
        if (this.dirty) {
            this.scheduleSave(0);
        }
    }

    // ------------------------------------------------------------------
    // Lines
    // ------------------------------------------------------------------

    sorted(productIds) {
        const position = (id) => this.model.product(id)?.position ?? Number.MAX_SAFE_INTEGER;
        return [...productIds].sort((a, b) => position(a) - position(b));
    }

    expectedQty(productId) {
        return this.state.expected[productId]?.qty ?? 0;
    }

    diff(productId) {
        const line = this.state.lines[productId];
        if (!line?.touched) {
            return 0;
        }
        const product = this.model.product(productId);
        return Math.round((countedQty(product, line) - this.expectedQty(productId)) * 100) / 100;
    }

    get progress() {
        const total = this.state.order.length;
        const done = this.state.order.filter((id) => this.state.lines[id]?.touched).length;
        const differing = this.state.order.filter((id) => this.diff(id) !== 0).length;
        return { total, done, left: total - done, differing, percent: total ? Math.round((100 * done) / total) : 0 };
    }

    get categoryNames() {
        return Object.fromEntries((this.desk.catalog?.categories || []).map((c) => [c.id, c.name]));
    }

    breakdown(product, parts) {
        if (!parts || (!parts.moved && !parts.sold)) {
            // Nothing moved or sold: the expected figure is what it had.
            return "";
        }
        const bits = [`had ${fmt(parts.opening)}`];
        if (parts.moved) {
            bits.push(`moved ${parts.moved > 0 ? "+" : "−"}${fmt(Math.abs(parts.moved))}`);
        }
        if (parts.sold) {
            bits.push(`sold ${fmt(parts.sold)}`);
        }
        return bits.join(" · ");
    }

    get lines() {
        const query = normalize(this.state.query.trim());
        const names = this.categoryNames;
        const result = [];
        let lastCategory = null;
        for (const id of this.state.order) {
            const product = this.model.product(id);
            const line = this.state.lines[id];
            const diff = this.diff(id);
            if (
                (this.state.filter === "todo" && line.touched) ||
                (this.state.filter === "diff" && !diff) ||
                (query && !normalize(product.name).includes(query))
            ) {
                continue;
            }
            const category = names[product.categ_id] || "";
            const parts = this.state.expected[id];
            result.push({
                key: String(id),
                productId: id,
                header: category !== lastCategory ? category : false,
                name: product.name,
                expected: stockLabel(product, parts?.qty ?? 0),
                negative: (parts?.qty ?? 0) < 0,
                breakdown: this.breakdown(product, parts),
                label: countLabel(product, line),
                diff: line.touched && diff ? diffLabel(product, diff) : "",
                state: !line.touched ? "untouched" : diff ? "changed" : "entered",
                touched: line.touched,
            });
            lastCategory = category;
        }
        return result;
    }

    get sections() {
        const sections = [];
        for (const line of this.lines) {
            if (line.header || !sections.length) {
                sections.push({ key: line.key, header: line.header || "", lines: [] });
            }
            sections[sections.length - 1].lines.push(line);
        }
        return sections;
    }

    get pickedIds() {
        return new Set(this.state.order);
    }

    get canSubmit() {
        return !this.state.busy && this.state.count && this.progress.left === 0;
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

    same(line) {
        const product = this.model.product(line.productId);
        const expected = this.expectedQty(product.id);
        if (expected < 0) {
            // Nothing can be counted below zero: count what is there.
            this.tap(line);
            return;
        }
        this.setLine(product.id, { ...countLineFor(product, expected), accepted: true });
    }

    keypadExpected() {
        const product = this.state.keypad.product;
        return stockLabel(product, this.expectedQty(product.id));
    }

    sameFromKeypad() {
        const product = this.state.keypad.product;
        this.state.keypad = null;
        this.same({ productId: product.id });
    }

    pick(product) {
        this.state.picking = false;
        this.state.keypad = { product, value: this.state.lines[product.id] || emptyCountLine() };
    }

    confirm(value) {
        this.setLine(this.state.keypad.product.id, { ...value, accepted: false });
        this.state.keypad = null;
    }

    setLine(productId, value) {
        this.state.lines[productId] = value;
        if (!this.state.order.includes(productId)) {
            this.state.order = this.sorted([...this.state.order, productId]);
            this.state.expected[productId] = this.state.expected[productId] || { opening: 0, moved: 0, sold: 0, qty: 0 };
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
                accepted: Boolean(line.accepted),
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
            await this.fetch("desk_count_save", { count_id: countId, lines: this.payload() });
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
        const { differing } = this.progress;
        const { id, bar, day_label } = this.state.count;
        const confirmed = await this.props.app.confirm(
            `Finish the ${bar.name} count?`,
            differing
                ? `${differing} item${differing > 1 ? "s differ" : " differs"} from what was expected: you explain them next.`
                : "Everything matches what was expected.",
            "Finish count"
        );
        if (!confirmed) {
            return;
        }
        browser.clearTimeout(this.saveTimer);
        this.state.busy = true;
        this.state.error = "";
        const outcome = await this.model.post(
            "desk_count_submit",
            { bar_id: this.barId, count_id: id, lines: this.payload() },
            `${bar.name} count for ${day_label}`
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
        if (outcome.status === "queued") {
            this.props.app.toast("No connection: the count is kept on this device and sent automatically.", "warning");
            this.props.app.back();
            return;
        }
        const differences = outcome.result.differences || 0;
        this.props.app.toast(
            differences ? `${bar.name} counted: ${differences} to explain.` : `${bar.name} counted: everything matches.`
        );
        if (differences) {
            this.props.app.replace("differences", { locationId: this.barId });
        } else {
            this.props.app.back();
        }
    }
}
