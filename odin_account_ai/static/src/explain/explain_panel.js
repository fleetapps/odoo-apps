import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { formatAmount, formatGrowth } from "@odin_account_pnl/pnl/pnl_format";
import { lineActionRegistry, sidePanelRegistry } from "@odin_account_pnl/pnl/pnl_registries";

/** A P&L line (Revenue, Gross Profit...) or an account directly under one. */
export function isExplainable(row) {
    if (!["line", "formula", "account"].includes(row.type)) {
        return false;
    }
    const segments = row.key.split("/");
    return segments.length <= 2 && ["L", "A"].includes(segments.at(-1).split(":")[0]);
}

/** The mode a parent must be unfolded in to show a child key segment. */
function modeFor(segment) {
    const [kind, dim, plan] = segment.split(":");
    if (kind === "A" || kind === "G") {
        return "accounts";
    }
    if (kind === "B") {
        return dim === "analytic" ? `analytic:${plan}` : dim;
    }
    return null;
}

/**
 * "Explain": why a figure moved against the period before. The drivers are
 * computed by the server and always add up to the change; the AI then
 * writes a short summary from them, citing them as [E3] chips that show
 * the row in the report or the entry in the side panel.
 */
export class ExplainPanel extends Component {
    static template = "odin_account_ai.ExplainPanel";
    static props = { row: Object, report: Object, close: Function };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            column: this.defaultColumn(),
            data: null,
            error: "",
            loading: false,
            story: null,
            storyError: "",
            writing: false,
        });
        onWillStart(() => this.load(this.props.row));
        onWillUpdateProps((next) => {
            if (next.row.key !== this.props.row.key) {
                return this.load(next.row);
            }
        });
    }

    get columns() {
        const columns = this.props.report.state.data?.columns || [];
        return columns.filter((column) => column.group !== "comparison");
    }

    defaultColumn() {
        return this.columns[0]?.key || "c0";
    }

    async load(row, column = this.state.column) {
        // Answers that arrive after another row was opened are ignored.
        const loadId = (this.loadId = (this.loadId || 0) + 1);
        this.state.loading = true;
        this.state.error = "";
        this.state.story = null;
        this.state.storyError = "";
        try {
            const data = await this.orm.call("odin.ai.explain", "explain", [
                this.props.report.state.options,
                row.key,
                column,
            ]);
            if (loadId !== this.loadId) {
                return;
            }
            this.state.data = data;
        } catch (error) {
            if (loadId !== this.loadId) {
                return;
            }
            this.state.data = null;
            this.state.error = error.data?.message || error.message || String(error);
        } finally {
            if (loadId === this.loadId) {
                this.state.loading = false;
            }
        }
        if (this.state.data && !this.state.data.ai && this.state.data.delta) {
            // Not awaited: the computed drivers show at once, the summary
            // fills in when the model answers.
            this.write();
        }
    }

    async changeColumn(ev) {
        this.state.column = ev.target.value;
        await this.load(this.props.row, this.state.column);
    }

    async write() {
        const loadId = this.loadId;
        this.state.writing = true;
        this.state.storyError = "";
        try {
            const story = await this.orm.call("odin.ai.explain", "narrate", [this.state.data]);
            if (loadId === this.loadId) {
                this.state.story = story;
            }
        } catch (error) {
            if (loadId === this.loadId) {
                this.state.storyError = error.data?.message || error.message || String(error);
            }
        } finally {
            if (loadId === this.loadId) {
                this.state.writing = false;
            }
        }
    }

    async continueInAsk() {
        const action = await this.orm.call("odin.ai.explain", "continue_in_ask", [this.state.data]);
        await this.action.doAction(action);
    }

    // ------------------------------------------------------------------
    // Display
    // ------------------------------------------------------------------

    fmt(value) {
        return formatAmount(value, { currencyDecimals: this.props.report.currencyDecimals });
    }

    signed(value) {
        const text = this.fmt(value);
        return value > 0 ? `+${text}` : text;
    }

    growth(value) {
        return formatGrowth(value);
    }

    /** Favourable or not for the line explained: a driver's effect has the
     * line's own sign, so it is good when it goes the line's good way. */
    goodClass(effect) {
        if (!effect || !this.state.data) {
            return "";
        }
        const greenOnPositive = this.state.data.good === null
            ? true
            : (this.state.data.delta > 0) === this.state.data.good;
        return (effect > 0) === greenOnPositive ? "o_odin_ai_good" : "o_odin_ai_bad";
    }

    barWidth(driver) {
        const max = Math.max(...this.state.data.drivers.map((d) => Math.abs(d.delta)), 0.01);
        return `${Math.round((Math.abs(driver.delta) / max) * 100)}%`;
    }

    /** "Revenue rose [E1] mainly…" as text and chip parts. */
    parts(text) {
        const result = [];
        const pattern = /\[(E\d+)\]/g;
        let last = 0;
        for (const match of text.matchAll(pattern)) {
            if (match.index > last) {
                result.push({ text: text.slice(last, match.index) });
            }
            if (this.state.data.handles[match[1]]) {
                result.push({ ref: match[1] });
            }
            last = match.index + match[0].length;
        }
        if (last < text.length) {
            result.push({ text: text.slice(last) });
        }
        return result;
    }

    handleLabel(ref) {
        return this.state.data.handles[ref]?.label || ref;
    }

    // ------------------------------------------------------------------
    // Following a reference
    // ------------------------------------------------------------------

    async openHandle(ref) {
        const item = this.state.data.handles[ref];
        if (!item) {
            return;
        }
        if (item.aml_id) {
            const row = this.props.report.rowByKey(`${this.props.row.key}/M:${item.aml_id}`);
            this.props.report.openPanel("peek", row || {
                key: `M:${item.aml_id}`,
                aml_id: item.aml_id,
                type: "entry",
                parent: null,
                name: item.label,
            });
        } else if (item.key) {
            await this.showRow(item.key);
        }
    }

    async showRow(key) {
        const report = this.props.report;
        const segments = key.split("/");
        for (let index = 1; index < segments.length; index++) {
            const parent = report.rowByKey(segments.slice(0, index).join("/"));
            const mode = modeFor(segments[index]);
            if (parent && parent.unfoldable && (!parent.unfolded || (mode && parent.mode !== mode))) {
                await report.unfold(parent, mode || parent.mode);
            }
        }
        if (report.rowByKey(key)) {
            report.state.focusKey = key;
            report.scrollIntoView(key, "center");
        }
    }
}

sidePanelRegistry.add("explain", {
    title: _t("Explain"),
    icon: "fa-magic",
    sequence: 30,
    Component: ExplainPanel,
    isAvailable: isExplainable,
});

lineActionRegistry.add("explain", {
    label: _t("Explain this change"),
    icon: "fa-magic",
    sequence: 30,
    hotkey: "e",
    isAvailable: isExplainable,
    run: (row, report) => report.openPanel("explain", row),
});
