import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { parseFloat as parseLocalizedFloat } from "@web/views/fields/parsers";
import { formatAmount, formatGrowth, formatPercent, sparkPoints } from "./pnl_format";

/**
 * The headline figures above the table: revenue, the bold subtotals and the
 * net result of the layout (odin.pnl.report._kpis), each with its change
 * against the comparison, its margin on revenue, its budget and, with the
 * 12-month trend on, a sparkline. A card shows its line in the table.
 */
export class PnlKpis extends Component {
    static template = "odin_account_pnl.PnlKpis";
    static props = {
        kpis: Array,
        fmt: Function,
        // { symbol, position: "before" | "after" }, as res.currency has them.
        currency: Object,
        focusKey: { optional: true },
        onSelect: Function,
    };

    setup() {
        this.formatGrowth = formatGrowth;
        this.formatPercent = formatPercent;
        this.sparkPoints = sparkPoints;
    }

    /** Green when the change helps the profit, red when not. */
    toneClass(kpi) {
        if (kpi.growth_good === null || kpi.growth_good === undefined) {
            return "o_odin_pnl_kpi_flat";
        }
        return kpi.growth_good ? "o_odin_pnl_good" : "o_odin_pnl_bad";
    }

    /** ↗ up, ↘ down, → no change: the direction, whatever its colour. */
    arrowClass(kpi) {
        if (!kpi.variance) {
            return "o_odin_pnl_kpi_arrow_flat";
        }
        return kpi.variance > 0 ? "o_odin_pnl_kpi_arrow_up" : "o_odin_pnl_kpi_arrow_down";
    }

    versus(kpi) {
        return _t("Against %s", kpi.previous_label);
    }

    /** "+12.5%", or the amount when there was nothing before. */
    change(kpi) {
        if (kpi.growth !== null && kpi.growth !== undefined) {
            return formatGrowth(kpi.growth);
        }
        if (kpi.variance) {
            const text = this.props.fmt(kpi.variance);
            return kpi.variance > 0 ? `+${text}` : text;
        }
        return "";
    }

}

/**
 * What makes a change material: at least a percent and at least an amount
 * (empty: 1% of revenue, computed by the server). Kept in the options, so a
 * saved view and its emails keep it too.
 */
export class PnlMaterialityMenu extends Component {
    static template = "odin_account_pnl.PnlMaterialityMenu";
    static components = { Dropdown };
    static props = {
        materiality: Object,
        options: Object,
        currencySymbol: String,
        currencyDecimals: Number,
        label: String,
        onApply: Function,
    };

    setup() {
        this.state = useState({ percent: "", amount: "", error: "" });
        this.dropdown = useDropdownState({ onOpen: () => this.onOpened() });
    }

    onOpened() {
        const { percent, amount } = this.props.options.materiality || {};
        this.state.percent = String(percent ?? 10);
        this.state.amount = amount === null || amount === undefined ? "" : String(amount);
        this.state.error = "";
    }

    get autoAmount() {
        return this.props.materiality.auto
            ? formatAmount(this.props.materiality.amount, { currencyDecimals: this.props.currencyDecimals, decimals: false })
            : "";
    }

    read(text) {
        const value = String(text || "").trim();
        if (!value) {
            return null;
        }
        const number = parseLocalizedFloat(value);
        if (!Number.isFinite(number) || number < 0) {
            throw new Error("negative");
        }
        return number;
    }

    apply() {
        let percent;
        let amount;
        try {
            percent = this.read(this.state.percent);
            amount = this.read(this.state.amount);
        } catch {
            this.state.error = _t("Type a positive number, or leave the amount empty.");
            return;
        }
        this.dropdown.close();
        this.props.onApply(percent ?? 10, amount);
    }

    reset() {
        this.dropdown.close();
        this.props.onApply(10, null);
    }

    onKeydown(ev) {
        if (ev.key === "Enter") {
            this.apply();
        }
    }
}
