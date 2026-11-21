import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { deserializeDate, serializeDate } from "@web/core/l10n/dates";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { CheckboxItem } from "@web/core/dropdown/checkbox_item";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";
import { useService } from "@web/core/utils/hooks";

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
    { scale: 1, decimals: true, label: (symbol) => _t("%s, with decimals", symbol), short: (symbol) => symbol },
    { scale: 1, decimals: false, label: (symbol) => _t("%s, whole amounts", symbol), short: (symbol) => symbol },
    { scale: 1000, decimals: false, label: (symbol) => _t("Thousands of %s (K)", symbol),
      short: (symbol) => _t("K%s", symbol) },
    { scale: 1000000, decimals: false, label: (symbol) => _t("Millions of %s (M)", symbol),
      short: (symbol) => _t("M%s", symbol) },
];

/**
 * The top bar of the P&L. One row of controls, each read as "label | value":
 * the period (with ‹ › to step), what to compare with and which budget (each
 * with an × to take it off again), how much detail, the Filters panel and the
 * Display menu, then the search. What narrows the journal items (journals,
 * partners, analytic accounts, draft entries) lives in the Filters panel and
 * shows under the bar as chips, each with its ×.
 *
 * The pickers in the panel are Odoo's MultiRecordSelector: its suggestions
 * render inside its own element (web/static/src/core/autocomplete), so a pick
 * is a click inside the panel and does not close it.
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
        this.nameService = useService("name");
        // Display names of the partners and analytic accounts filtered on,
        // for their chips.
        this.state = useState({ newBudget: "", names: {} });
        // What the menus read while open. Odoo's Dropdown renders an open
        // menu from the slot it was opened with, and Owl's slot keeps the
        // props of that render (owl.js, capture()): read from props, a
        // menu that stays open (a checkbox, a picker) showed the options as
        // they were when it opened. This state is one object for every
        // render, updated with the props, so open menus read it live.
        this.live = useState({ options: this.props.options, data: this.props.data });
        onWillStart(() => this.loadNames(this.props.options));
        onWillUpdateProps((next) => {
            this.live.options = next.options;
            this.live.data = next.data;
            return this.loadNames(next.options);
        });
    }

    async loadNames(options) {
        const wanted = [
            ["res.partner", options.partner_ids || []],
            ["account.analytic.account", options.analytic_account_ids || []],
        ];
        for (const [model, ids] of wanted) {
            if (ids.length) {
                const names = await this.nameService.loadDisplayNames(model, ids);
                this.state.names = { ...this.state.names, [model]: { ...this.state.names[model], ...names } };
            }
        }
    }

    get options() {
        return this.live.options;
    }

    get data() {
        return this.live.data;
    }

    get periodLabel() {
        const columns = this.data.columns || [];
        const current = columns.filter((column) => column.group === "current");
        if (current.length > 1) {
            // Divided into months or quarters.
            const preset = PRESETS.find(([key]) => key === this.options.date.filter);
            return preset ? preset[1] : `${current[0].label} – ${current.at(-1).label}`;
        }
        return this.data.period_label;
    }

    /** The name of each control, before its value. */
    get labels() {
        return {
            compare: _t("Compare"),
            detail: _t("Detail"),
            budget: _t("Budget"),
        };
    }

    get comparisonLabel() {
        if (this.options.by_company) {
            return _t("Companies side by side");
        }
        const comparison = this.options.comparison;
        const labels = {
            none: _t("None"),
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
        const budget = this.data.budgets.find((b) => b.id === this.options.budget_id);
        return budget ? budget.name : _t("None");
    }

    get layoutLabel() {
        const layout = this.data.layouts.find((l) => l.id === this.options.layout_id);
        return layout ? layout.name : "";
    }

    get scaleLabel() {
        const symbol = this.data.currency.symbol;
        const found =
            this.scales.find((s) => s.scale === this.options.scale && s.decimals === this.options.decimals) ||
            this.scales.find((s) => s.scale === this.options.scale) ||
            this.scales[0];
        return found.short(symbol);
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

    get comparisonActive() {
        const options = this.options;
        return options.comparison.mode !== "none" || options.ytd || options.by_company;
    }

    /** Back to the period alone: no comparison, no year to date, one column. */
    clearComparison() {
        this.props.onChange({
            comparison: { ...this.options.comparison, mode: "none" },
            ytd: false,
            by_company: false,
        });
    }

    // ---- filters: journals, partners, analytic, entries -----------------

    toggleJournal(journalId) {
        const ids = new Set(this.options.journal_ids);
        ids.has(journalId) ? ids.delete(journalId) : ids.add(journalId);
        this.props.onChange({ journal_ids: [...ids] });
    }

    setAnalytic(ids) {
        this.props.onChange({ analytic_account_ids: ids });
    }

    setPartners(ids) {
        this.props.onChange({ partner_ids: ids });
    }

    /** "Acme", "Acme, Globex" or "Acme +2". */
    namesLabel(names) {
        if (names.length <= 2) {
            return names.join(", ");
        }
        return _t("%(first)s +%(count)s", { first: names[0], count: names.length - 1 });
    }

    recordNames(model, ids) {
        const known = this.state.names[model] || {};
        return ids.map((id) => (typeof known[id] === "string" ? known[id] : "…"));
    }

    /** What narrows the journal items now, each with what takes it off. */
    get activeFilters() {
        const options = this.options;
        const filters = [];
        if (options.journal_ids.length) {
            const names = options.journal_ids.map(
                (id) => this.props.journals.find((journal) => journal.id === id)?.label || "…");
            filters.push({ key: "journals", label: _t("Journals"), value: this.namesLabel(names),
                clear: () => this.props.onChange({ journal_ids: [] }) });
        }
        if (options.partner_ids.length) {
            filters.push({ key: "partners", label: _t("Partners"),
                value: this.namesLabel(this.recordNames("res.partner", options.partner_ids)),
                clear: () => this.setPartners([]) });
        }
        if (options.analytic_account_ids.length) {
            filters.push({ key: "analytic", label: _t("Analytic"),
                value: this.namesLabel(this.recordNames("account.analytic.account", options.analytic_account_ids)),
                clear: () => this.setAnalytic([]) });
        }
        if (options.include_draft) {
            filters.push({ key: "draft", label: _t("Entries"), value: _t("Posted and draft"),
                clear: () => this.props.onChange({ include_draft: false }) });
        }
        return filters;
    }

    clearFilters() {
        this.props.onChange({ journal_ids: [], partner_ids: [], analytic_account_ids: [], include_draft: false });
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
