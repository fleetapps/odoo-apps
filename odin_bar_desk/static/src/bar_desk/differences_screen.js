import { Component, onWillStart, useState } from "@odoo/owl";
import { errorMessage, feedback } from "./desk_model";
import { Keypad } from "./keypad";
import { countLabel, diffLabel, fmt, money, stockLabel } from "./utils";

/**
 * Every difference between what was counted and what was expected, for the
 * day on the sheet. Each gets a reason in one tap: most only record why (the
 * difference is written off under that reason when the day is approved);
 * "Missed move" logs the transfer nobody wrote down, "Counting mistake"
 * corrects the count. When one location is short by exactly what another has
 * too much of, the move that was missed is suggested.
 */
export class DifferencesScreen extends Component {
    static template = "odin_bar_desk.DifferencesScreen";
    static components = { Keypad };
    static props = { app: Object, params: { type: Object, optional: true } };

    setup() {
        this.model = this.props.app.model;
        this.desk = useState(this.model.state);
        this.fmt = fmt;
        this.money = money;
        this.state = useState({
            loading: true,
            lines: [],
            suggestions: [],
            locationId: this.props.params?.locationId || null,
            openLine: null,
            keypad: null,
            busy: false,
            error: "",
        });
        onWillStart(() => this.load());
    }

    async load() {
        try {
            const data = await this.model.fetch("desk_differences", { business_date: this.desk.home.day.date });
            Object.assign(this.state, { lines: data.lines, suggestions: data.suggestions });
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    get reasons() {
        return this.desk.catalog?.variance_reasons || [];
    }

    get locations() {
        const seen = new Map();
        for (const line of this.state.lines) {
            seen.set(line.bar.id, line.bar);
        }
        return [...seen.values()];
    }

    get lines() {
        const lines = this.state.locationId
            ? this.state.lines.filter((line) => line.bar.id === this.state.locationId)
            : this.state.lines;
        return lines.map((line) => {
            const product = this.model.product(line.product_id);
            const reason = this.model.varianceReason(line.reason_id);
            return {
                ...line,
                product,
                name: product?.name || "",
                expectedLabel: stockLabel(product, line.expected),
                countedLabel: stockLabel(product, line.counted),
                countedDetail: countLabel(product, { ...line, touched: true }),
                diffLabel: diffLabel(product, line.diff),
                reasonName: reason?.name || "",
            };
        });
    }

    get unresolved() {
        return this.state.lines.filter((line) => !line.reason_id).length;
    }

    get suggestions() {
        return this.state.suggestions
            .filter(
                (s) => !this.state.locationId || s.from.id === this.state.locationId || s.to.id === this.state.locationId
            )
            .map((s) => {
                const product = this.model.product(s.product_id);
                return { ...s, product, label: stockLabel(product, s.qty) };
            });
    }

    toggle(line) {
        this.state.openLine = this.state.openLine === line.line_id ? null : line.line_id;
    }

    async explain(line, reason, note = undefined) {
        if (reason.action === "move") {
            return this.logMissedMove(line);
        }
        if (reason.action === "recount") {
            this.state.keypad = { line, product: line.product, value: { ...line, touched: true } };
            return;
        }
        await this.write(line, reason.id, note ?? line.note);
        this.state.openLine = null;
    }

    async write(line, reasonId, note) {
        this.state.error = "";
        try {
            const result = await this.model.fetch("desk_explain", {
                line_id: line.line_id,
                reason_id: reasonId || false,
                note: note || false,
            });
            const own = this.state.lines.find((other) => other.line_id === line.line_id);
            Object.assign(own, { reason_id: result.reason_id, note: result.note });
            feedback(true);
        } catch (error) {
            feedback(false);
            this.state.error = errorMessage(error);
        }
    }

    async saveNote(line, ev) {
        const note = ev.target.value.trim();
        if (note !== (line.note || "") && line.reason_id) {
            await this.write(line, line.reason_id, note);
        }
    }

    /** Short here: it went somewhere. Too much here: it came from somewhere. */
    logMissedMove(line) {
        const short = line.diff < 0;
        this.props.app.go("move", {
            fromId: short ? line.bar.id : null,
            toId: short ? null : line.bar.id,
            onSheetDay: true,
            items: [{ productId: line.product_id, uomId: line.product.uom.id, qty: Math.abs(line.diff) }],
        });
    }

    async acceptSuggestion(suggestion) {
        const confirmed = await this.props.app.confirm(
            "Record the missed move?",
            `${suggestion.label} of ${suggestion.product.name} from ${suggestion.from.name} to ${suggestion.to.name}, on ${this.desk.home.day.label}.`,
            "Record move"
        );
        if (!confirmed) {
            return;
        }
        this.state.busy = true;
        const outcome = await this.model.post(
            "desk_move",
            {
                from_bar_id: suggestion.from.id,
                to_bar_id: suggestion.to.id,
                business_date: this.desk.home.day.date,
                note: "Missed move, found at the count",
                lines: [{ product_id: suggestion.product_id, uom_id: suggestion.product.uom.id, qty: suggestion.qty }],
            },
            `Move ${suggestion.from.code} → ${suggestion.to.code}`
        );
        this.state.busy = false;
        if (outcome.status === "failed") {
            feedback(false);
            this.state.error = outcome.message;
            return;
        }
        feedback(true);
        this.props.app.toast("Move recorded: both differences are gone.");
        this.state.loading = true;
        await this.load();
    }

    async confirmRecount(value) {
        const { line } = this.state.keypad;
        this.state.keypad = null;
        this.state.error = "";
        try {
            await this.model.fetch("desk_count_fix", { line_id: line.line_id, value });
            feedback(true);
            this.state.loading = true;
            await this.load();
        } catch (error) {
            feedback(false);
            this.state.error = errorMessage(error);
        }
    }

    done() {
        this.props.app.back();
    }
}
