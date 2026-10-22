import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { deserializeDate, serializeDate } from "@web/core/l10n/dates";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { CheckboxItem } from "@web/core/dropdown/checkbox_item";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";

export const PRESETS = [
    ["this_month", _t("This month")],
    ["previous_month", _t("Last month")],
    ["this_quarter", _t("This quarter")],
    ["previous_quarter", _t("Last quarter")],
    ["this_year", _t("This financial year")],
    ["previous_year", _t("Last financial year")],
    ["year_to_date", _t("Financial year to date")],
];

export const SCALES = [
    { scale: 1, decimals: true, label: (symbol) => _t("In .%s", symbol) },
    { scale: 1, decimals: false, label: (symbol) => _t("In %s", symbol) },
    { scale: 1000, decimals: false, label: (symbol) => _t("In K%s", symbol) },
    { scale: 1000000, decimals: false, label: (symbol) => _t("In M%s", symbol) },
];

/**
 * The top bar of the P&L, in the order of Odoo 19 Enterprise's report
 * filters: period, comparison, journals, analytic, entries, budget, layout,
 * scale. Analytic and partner pickers open in a drawer under the bar so their
 * autocomplete is not fighting a dropdown for the clicks.
 * https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting.html#report-filters-and-options
 */
export class PnlFilterBar extends Component {
    static template = "odin_account_pnl.PnlFilterBar";
    static components = { Dropdown, DropdownItem, CheckboxItem, DateTimeInput, MultiRecordSelector };
    static props = {
        options: Object,
        data: Object,
        journals: Array,
        onChange: Function,
        onCreateBudget: Function,
        search: String,
        onSearch: Function,
    };

    setup() {
        this.presets = PRESETS;
        this.scales = SCALES;
        this.state = useState({ drawer: null, newBudget: "" });
    }

    get options() {
        return this.props.options;
    }

    get periodLabel() {
        const columns = this.props.data.columns || [];
        const current = columns.filter((column) => column.group === "current");
        if (current.length > 1) {
            // Divided into months or quarters.
            const preset = PRESETS.find(([key]) => key === this.options.date.filter);
            return preset ? preset[1] : `${current[0].label} – ${current.at(-1).label}`;
        }
        return this.props.data.period_label;
    }

    get comparisonLabel() {
        if (this.options.by_company) {
            return _t("Companies side by side");
        }
        const comparison = this.options.comparison;
        const labels = {
            none: _t("Comparison"),
            previous_period: _t("Previous period"),
            previous_year: _t("Same period last year"),
            custom: _t("Custom comparison"),
        };
        const label = labels[comparison.mode];
        const text = comparison.mode !== "none" && comparison.mode !== "custom" && comparison.periods > 1
            ? `${label} ×${comparison.periods}`
            : label;
        return this.options.ytd ? `${comparison.mode === "none" ? _t("Year to date") : text + " + " + _t("YTD")}` : text;
    }

    get journalLabel() {
        const ids = this.options.journal_ids;
        if (!ids.length) {
            return _t("All Journals");
        }
        if (ids.length === 1) {
            const journal = this.props.journals.find((j) => j.id === ids[0]);
            return journal ? journal.name : _t("1 journal");
        }
        return _t("%s journals", ids.length);
    }

    get entriesLabel() {
        return this.options.include_draft ? _t("Posted and draft entries") : _t("Posted entries");
    }

    /** [level, label, what it shows]: see DETAIL_LEVELS in odin_pnl_report.py. */
    get detailLevels() {
        return [
            ["lines", _t("Lines"), _t("Revenue, costs and profit: the summary")],
            ["accounts", _t("Accounts"), _t("Every account under its line")],
            ["transactions", _t("Every transaction"), _t("Every journal item under its account, with totals")],
        ];
    }

    get detailLabel() {
        return this.detailLevels.find(([level]) => level === this.options.detail)?.[1] || _t("Lines");
    }

    get budgetLabel() {
        const budget = this.props.data.budgets.find((b) => b.id === this.options.budget_id);
        return budget ? budget.name : _t("Budget");
    }

    get layoutLabel() {
        const layout = this.props.data.layouts.find((l) => l.id === this.options.layout_id);
        return layout ? layout.name : "";
    }

    get scaleLabel() {
        const symbol = this.props.data.currency.symbol;
        const found =
            this.scales.find((s) => s.scale === this.options.scale && s.decimals === this.options.decimals) ||
            this.scales.find((s) => s.scale === this.options.scale) ||
            this.scales[0];
        return found.label(symbol);
    }

    isScale(item) {
        return item.scale === this.options.scale && (item.scale !== 1 || item.decimals === this.options.decimals);
    }

    // ---- period --------------------------------------------------------

    setPreset(preset) {
        this.props.onChange({ date: { filter: preset }, unfolded: [], modes: {} });
    }

    step(direction) {
        this.props.onChange({ date: { ...this.options.date, filter: "custom", step: direction } });
    }

    get dateFrom() {
        return deserializeDate(this.options.date.date_from);
    }

    get dateTo() {
        return deserializeDate(this.options.date.date_to);
    }

    setCustomDate(which, value) {
        if (!value) {
            return;
        }
        const date = { ...this.options.date, filter: "custom" };
        date[which] = serializeDate(value);
        this.props.onChange({ date });
    }

    setDivide(divide) {
        this.props.onChange({ divide, by_company: divide === "none" && this.options.by_company });
    }

    // ---- comparison ----------------------------------------------------

    setComparison(mode) {
        const comparison = { ...this.options.comparison, mode };
        if (mode === "custom" && !comparison.date_from) {
            comparison.date_from = this.options.date.date_from;
            comparison.date_to = this.options.date.date_to;
        }
        // Companies side by side are columns too: a comparison replaces them.
        this.props.onChange({
            comparison,
            divide: mode === "none" ? this.options.divide : "none",
            by_company: mode === "none" && this.options.by_company,
        });
    }

    setComparisonCount(event) {
        const periods = Math.min(Math.max(parseInt(event.target.value) || 1, 1), 12);
        this.props.onChange({ comparison: { ...this.options.comparison, periods } });
    }

    setComparisonDate(which, value) {
        if (!value) {
            return;
        }
        const comparison = { ...this.options.comparison, mode: "custom" };
        comparison[which] = serializeDate(value);
        this.props.onChange({ comparison });
    }

    get comparisonFrom() {
        return this.options.comparison.date_from ? deserializeDate(this.options.comparison.date_from) : false;
    }

    get comparisonTo() {
        return this.options.comparison.date_to ? deserializeDate(this.options.comparison.date_to) : false;
    }

    // ---- journals, analytic, partners ----------------------------------

    toggleJournal(journalId) {
        const ids = new Set(this.options.journal_ids);
        ids.has(journalId) ? ids.delete(journalId) : ids.add(journalId);
        this.props.onChange({ journal_ids: [...ids] });
    }

    toggleDrawer(name) {
        this.state.drawer = this.state.drawer === name ? null : name;
    }

    setAnalytic(ids) {
        this.props.onChange({ analytic_account_ids: ids });
    }

    setPartners(ids) {
        this.props.onChange({ partner_ids: ids });
    }

    // ---- options, budget, layout, scale --------------------------------

    toggleOption(name) {
        this.props.onChange({ [name]: !this.options[name] });
    }

    setBudget(budgetId) {
        this.props.onChange({ budget_id: budgetId });
    }

    async createBudget() {
        const name = this.state.newBudget.trim();
        if (name) {
            this.state.newBudget = "";
            await this.props.onCreateBudget(name);
        }
    }

    onBudgetKey(ev) {
        if (ev.key === "Enter") {
            this.createBudget();
        }
    }

    setLayout(layoutId) {
        this.props.onChange({ layout_id: layoutId, unfolded: [], modes: {} });
    }

    setScale(item) {
        this.props.onChange({ scale: item.scale, decimals: item.decimals });
    }
}
