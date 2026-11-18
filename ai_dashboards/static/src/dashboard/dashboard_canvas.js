/** @odoo-module **/
/**
 * The dashboard page.
 *
 * A field widget on ai.dashboard's form, so the page keeps everything a form
 * gives for free — a deep link, breadcrumbs, the record's save and discard,
 * the unsaved-changes guard — while drawing the whole page itself: the header
 * and its actions, the filters, the highlights and the tiles. The form around
 * it has no sheet, no header and no chatter (see ai_dashboard_views.xml).
 *
 * Record actions (Keep it, Duplicate, Email me this, …) are Odoo's own
 * ViewButton, so they keep a form button's behaviour exactly: the record is
 * saved first, `confirm` asks, and the form reloads after.
 *
 * Drawing is split by job: format.js turns numbers into words, chart_theme.js
 * builds Chart.js configurations, highlights.js writes the highlight
 * sentences, widgets.js holds the chart and sparkline components. Tile
 * templates are sub-templates of this one (dashboard_canvas.xml).
 */
import { Component, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { MultiRecordSelector } from "@web/core/record_selectors/multi_record_selector";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { KeepLast } from "@web/core/utils/concurrency";
import { useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/core/utils/numbers";
import { useSortable } from "@web/core/utils/sortable_owl";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { ViewButton } from "@web/views/view_button/view_button";
import { cartesianConfig, readTokens, ringConfig } from "./chart_theme";
import { axisLabels, compactNumber, describeChange, formatParts, formatValue } from "./format";
import { computeHighlights } from "./highlights";
import { AiChart, AiSparkline } from "./widgets";

// Chart.js ships with Odoo but is lazy-loaded, as the graph view loads it.
const CHART_BUNDLE = "web.chartjs_lib";

const PERIOD_OPTIONS = [
    ["today", _t("Today")],
    ["this_week", _t("This week")],
    ["last_week", _t("Last week")],
    ["last_7_days", _t("Last 7 days")],
    ["this_month", _t("This month")],
    ["last_month", _t("Last month")],
    ["last_30_days", _t("Last 30 days")],
    ["this_quarter", _t("This quarter")],
    ["last_quarter", _t("Last quarter")],
    ["last_90_days", _t("Last 90 days")],
    ["this_year", _t("This year")],
    ["last_year", _t("Last year")],
    ["all_time", _t("All time")],
];
// Two types in one family draw from the same figures, so switching between
// them repaints locally; crossing families needs the server.
const SHAPE_FAMILY = {
    kpi: "kpi", table: "table", pivot: "pivot",
    bar: "chart", line: "chart", pie: "chart", donut: "chart",
    pnl: "pnl", aging: "aging", banks: "banks",
};
const COMPARE_MODES = [
    ["none", _t("No comparison")],
    ["previous_period", _t("vs previous period")],
    ["previous_year", _t("vs last year")],
];
// Cards that show today's position take no period.
const POSITION_CARDS = ["aging", "banks"];
const CHART_TYPES = ["bar", "line", "pie", "donut"];
const FORMAT_KINDS = ["plain", "integer", "monetary", "percent"];
const FORMAT_LABELS = {
    plain: _t("a plain number"),
    integer: _t("a whole number"),
    monetary: _t("money"),
    percent: _t("a percentage"),
};
const ROW_LIMITS = [5, 10, 20, 50];
const GRANULARITIES = ["day", "week", "month", "quarter", "year"];
const GRANULARITY_LABELS = {
    day: _t("by day"), week: _t("by week"), month: _t("by month"),
    quarter: _t("by quarter"), year: _t("by year"),
};
const SIZES = [[3, _t("Small")], [4, _t("Medium")], [6, _t("Half")], [8, _t("Wide")], [12, _t("Full width")]];
const SERIES_SLOTS = 8;
// A horizontal bar reads long names; a vertical one, short ones and dates.
const LONG_LABEL = 14;
const MANY_BARS = 8;

export class AIDashboardCanvas extends Component {
    static template = "ai_dashboards.Canvas";
    static components = { AiChart, AiSparkline, Dropdown, DropdownItem, MultiRecordSelector, ViewButton };
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.rootRef = useRef("root");
        this.gridRef = useRef("grid");
        this.keepLast = new KeepLast();

        this.state = useState({
            loading: true,
            error: null,
            data: null,
            // Bumped on every new set of figures, so each chart redraws once.
            stamp: 0,
            filters: {},
            // Per-pivot paging. View state, never part of the dashboard.
            offsets: {},
            // A saved dashboard is for reading; tools appear only on request.
            editMode: false,
            editing: null,
            renamingTitle: false,
            // Set the moment the spec on screen stops matching the database.
            pending: false,
            highlight: 0,
            isAdmin: false,
        });

        onWillStart(async () => {
            const [, isAdmin] = await Promise.all([
                loadBundle(CHART_BUNDLE),
                user.hasGroup("ai_dashboards.group_dashboard_admin"),
            ]);
            this.state.isAdmin = isAdmin;
            await this.load();
        });

        // Put the cursor in a rename box the moment it opens, old text selected.
        this.renamerRef = useRef("renamer");
        useEffect(
            (el) => {
                if (el) {
                    el.focus();
                    el.select();
                }
            },
            () => [this.renamerRef.el]
        );

        // Drag a tile by its handle to move it. Only while editing.
        useSortable({
            ref: this.gridRef,
            elements: ".o_ai_tile",
            handle: ".o_ai_drag",
            enable: () => this.showTools,
            cursor: "grabbing",
            onDrop: ({ element, previous }) =>
                this.moveAfter(element.dataset.id, previous ? previous.dataset.id : null),
        });

        // If the figures held cannot draw the spec now on screen — a tile
        // turned from a chart into a pivot, one the assistant just added, a
        // discard back to an older spec — fetch new ones. Anything that is
        // only presentation is drawn from the spec with no round trip.
        useRecordObserver(() => {
            if (this.staleFigures()) {
                this.load();
            }
        });
    }

    /** Words handed to components as props, where a template's own text
     *  would not be translated. */
    get labels() {
        return {
            keep: _t("Keep it"),
            discard: _t("Discard"),
            discardConfirm: _t("Discard this draft? It was never added to your app tile."),
            share: _t("Share"),
            more: _t("More actions"),
            archive: _t("Archive"),
            archiveConfirm: _t("Archive this dashboard? People it is shared with will no longer see it. You can restore it from the Archived filter."),
            renameTitle: _t("Click to rename"),
            filters: _t("Filters"),
            compare: _t("Compare"),
            highlights: _t("Highlights"),
            highlight: _t("Highlight"),
            drag: _t("Drag to move"),
            period: _t("Period"),
            colour: _t("Change this tile's colour"),
            tileMenu: _t("More options for this tile"),
            now: _t("Now"),
            comparison: _t("Comparison"),
            netProfit: _t("Net profit"),
            previousRows: _t("Previous rows"),
            nextRows: _t("Next rows"),
            previousCols: _t("Previous columns"),
            nextCols: _t("Next columns"),
        };
    }

    // ------------------------------------------------------------- record
    get record() {
        return this.props.record;
    }

    get dashboardId() {
        return this.record.resId;
    }

    get spec() {
        try {
            return JSON.parse(this.record.data[this.props.name] || "{}");
        } catch {
            return {};
        }
    }

    get title() {
        return this.record.data.name || "";
    }

    get description() {
        return this.record.data.description || "";
    }

    get isDraft() {
        return this.record.data.state === "draft";
    }

    get sharedPeople() {
        const list = this.record.data.share_user_ids;
        return (list && list.records ? list.records : []).slice(0, 4).map((r) => ({
            id: r.resId,
            name: r.data.display_name || "",
            src: `/web/image/res.users/${r.resId}/avatar_128`,
        }));
    }

    get sharedCount() {
        return this.record.data.shared_count || 0;
    }

    get isEditable() {
        return !this.props.readonly && !!this.state.data
            && (this.state.data.is_owner || this.state.isAdmin);
    }

    get showTools() {
        return this.isEditable && this.state.editMode;
    }

    setEditMode(on) {
        this.state.editMode = on;
        if (!on) {
            this.state.editing = null;
            this.state.renamingTitle = false;
        }
    }

    /** Edits made here and not yet written to the database. */
    get hasUnsavedEdits() {
        return this.state.pending && !!this.record.dirty;
    }

    // ------------------------------------------------------------- actions
    /** The ⋯ menu: everything you do to a dashboard rather than read in it. */
    get menuActions() {
        const d = this.record.data;
        const items = [];
        if (d.state !== "draft") {
            items.push(d.is_subscribed
                ? { key: "unsub", name: "action_unsubscribe", string: _t("Stop emailing me") }
                : { key: "sub", name: "action_subscribe", string: _t("Email me this") });
            items.push({ key: "dup", name: "action_duplicate", string: _t("Duplicate") });
        }
        if ((d.version_count || 0) > 1) {
            items.push({ key: "hist", name: "action_open_history", string: _t("History") });
        }
        items.push({ key: "about", name: "action_open_about", string: _t("What it reads") });
        if (this.state.isAdmin && d.state !== "draft") {
            items.push(d.is_pinned
                ? { key: "unpin", name: "action_unpin", string: _t("Unpin from menu") }
                : { key: "pin", name: "action_pin_to_menu", string: _t("Pin to menu") });
        }
        return items;
    }

    get canArchive() {
        return this.isEditable && !this.isDraft;
    }

    /** The dashboard as a file: the spec is the portable unit. */
    async exportSpec() {
        const payload = await this.orm.call("ai.dashboard", "action_export_spec", [[this.dashboardId]]);
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${(this.title || "dashboard").replace(/[^\w-]+/g, "_")}.json`;
        link.click();
        URL.revokeObjectURL(url);
    }

    // ------------------------------------------------------- title rename
    startTitleRename() {
        if (this.showTools) {
            this.state.renamingTitle = true;
        }
    }

    async commitTitle(value) {
        const clean = (value || "").trim();
        this.state.renamingTitle = false;
        if (clean && clean !== this.title) {
            this.state.pending = true;
            await this.record.update({ name: clean });
        }
    }

    onTitleKey(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.commitTitle(ev.target.value);
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            this.state.renamingTitle = false;
        }
    }

    onTitleBlur(ev) {
        if (this.state.renamingTitle) {
            this.commitTitle(ev.target.value);
        }
    }

    // ------------------------------------------------------------- loading
    get widgets() {
        const drawn = new Map(((this.state.data && this.state.data.widgets) || []).map((w) => [w.id, w]));
        return (this.spec.widgets || []).map((w) => {
            const figures = drawn.get(w.id);
            const usable = figures && SHAPE_FAMILY[figures.type] === SHAPE_FAMILY[w.type];
            return {
                ...(usable ? figures : {}),
                id: w.id,
                // What it looks like comes from the spec on screen, so an
                // edit shows the instant it is made and a discard undoes it.
                type: w.type,
                title: w.title,
                caption: w.caption,
                period: (usable && figures.period) || w.period,
                span: Math.max(1, Math.min(12, w.span || 6)),
                color: w.color,
                format: w.format || { kind: "plain" },
                polarity: w.polarity || "up_is_good",
                drill: w.drill !== false,
                group_count: ((w.query || {}).group_by || []).length,
                query: w.query || {},
                calculating: !usable && !(figures && figures.error),
                error: (figures && figures.error) || undefined,
            };
        });
    }

    staleFigures() {
        if (!this.state.data) {
            return false;
        }
        const drawn = new Map(this.state.data.widgets.map((w) => [w.id, w]));
        return (this.spec.widgets || []).some((w) => {
            const figures = drawn.get(w.id);
            return !figures || SHAPE_FAMILY[figures.type] !== SHAPE_FAMILY[w.type];
        });
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            if (!this.dashboardId) {
                this.state.data = null;
                return;
            }
            this.state.data = await this.keepLast.add(this.orm.call(
                "ai.dashboard.render",
                "render",
                [this.dashboardId, this.state.filters, this.state.offsets,
                 // Draw what is on screen, not what happens to be stored.
                 this.hasUnsavedEdits ? this.spec : null]
            ));
            this.state.stamp++;
            this.state.highlight = 0;
        } catch (error) {
            this.state.error = (error && error.data && error.data.message)
                || _t("This dashboard could not be calculated.");
        } finally {
            this.state.loading = false;
        }
    }

    get currency() {
        return (this.state.data && this.state.data.currency) || {};
    }

    get tookMs() {
        return this.state.data ? this.state.data.took_ms : null;
    }

    // ------------------------------------------------------------ filters
    get filterBar() {
        return (this.state.data && this.state.data.filters) || [];
    }

    get periodOptions() {
        return PERIOD_OPTIONS;
    }

    get allPlaceholder() {
        return _t("All");
    }

    /** The reader's own choice the instant they make it, else the server's. */
    filterValue(flt) {
        return flt.key in this.state.filters ? this.state.filters[flt.key] : flt.value;
    }

    recordIds(flt) {
        const value = this.filterValue(flt);
        return (Array.isArray(value) ? value : []).filter((id) => Number.isInteger(id) && id > 0);
    }

    periodName(value) {
        const found = PERIOD_OPTIONS.find((option) => option[0] === value);
        return found ? found[1] : value;
    }

    selectionName(flt) {
        const value = this.filterValue(flt);
        const found = (flt.options || []).find((option) => option[0] === value);
        return found ? found[1] : _t("All");
    }

    isNarrowed(flt) {
        if (flt.available === false) {
            return false;
        }
        if (flt.type === "many2one") {
            return this.recordIds(flt).length > 0;
        }
        return flt.type === "selection" && Boolean(this.filterValue(flt));
    }

    async setFilter(key, value) {
        this.state.filters = { ...this.state.filters, [key]: value };
        await this.load();
    }

    get canResetFilters() {
        return this.filterBar.some((flt) => flt.key in this.state.filters);
    }

    async resetFilters() {
        const kept = { ...this.state.filters };
        for (const flt of this.filterBar) {
            delete kept[flt.key];
        }
        this.state.filters = kept;
        await this.load();
    }

    get anyComparable() {
        return this.widgets.some((w) => w.comparable);
    }

    get compareModes() {
        return COMPARE_MODES;
    }

    get compareMode() {
        return (this.state.data && this.state.data.compare) || "none";
    }

    /** "vs Oct 2026" when the server named the window, else the mode. */
    get compareToggleLabel() {
        const mode = this.compareMode;
        if (mode === "none") {
            return _t("No comparison");
        }
        const named = this.widgets.find((w) => w.compare_label);
        if (named) {
            return _t("vs %s", named.compare_label);
        }
        const found = COMPARE_MODES.find((m) => m[0] === mode);
        return found ? found[1] : mode;
    }

    async setCompare(mode) {
        await this.setFilter("__compare", mode);
    }

    async setCardPeriod(widget, value) {
        await this.setFilter(`__period:${widget.id}`, value);
    }

    // --------------------------------------------------------- highlights
    get highlights() {
        if (!this.state.data || this.showTools) {
            return [];
        }
        return computeHighlights(this.widgets, this.currency);
    }

    get currentHighlight() {
        const list = this.highlights;
        return list[Math.min(this.state.highlight, list.length - 1)];
    }

    // ------------------------------------------------------------- tiles
    spanClass(widget) {
        return `o_ai_w${widget.span}`;
    }

    format(value, widget, options) {
        return formatValue(value, widget.format, this.currency, options);
    }

    parts(value, widget) {
        return formatParts(value, widget.format, this.currency, { compact: true });
    }

    total(widget) {
        if (widget.value !== undefined && widget.value !== null && !widget.series) {
            return widget.value;
        }
        return (widget.series || []).reduce((sum, point) => sum + (point.value || 0), 0);
    }

    chartVersion(widget) {
        return [this.state.stamp, widget.type, widget.color, widget.span,
                widget.format && widget.format.kind, this.compareMode].join("|");
    }

    isDateAxis(widget) {
        return (widget.series || []).some((p) => typeof p.raw === "string" && /^\d{4}-\d{2}-\d{2}/.test(p.raw));
    }

    /** A captioned line at a third of the page or less runs edge to edge. */
    isHero(widget) {
        return widget.type === "line" && widget.span <= 4 && Boolean(widget.caption || widget.period);
    }

    isHorizontal(widget) {
        if (widget.type !== "bar" || this.isDateAxis(widget)) {
            return false;
        }
        const series = widget.series || [];
        return series.length > MANY_BARS || series.some((p) => (p.label || "").length > LONG_LABEL);
    }

    /** A line needs two points; one is a figure, not a trend. */
    isSinglePoint(widget) {
        return widget.type === "line" && (widget.series || []).length < 2;
    }

    chartHeight(widget) {
        if (this.isHorizontal(widget)) {
            return Math.max(160, (widget.series || []).length * 30 + 24);
        }
        return this.isHero(widget) ? 128 : 240;
    }

    buildCartesian(widget) {
        return (el) => {
            const series = widget.series || [];
            return cartesianConfig(readTokens(el), {
                type: widget.type === "line" ? "line" : "bar",
                labels: axisLabels(series),
                titles: series.map((p) => p.label),
                values: series.map((p) => p.value),
                compare: widget.compare_series || null,
                compareLabel: widget.compare_label || _t("Comparison"),
                label: widget.title,
                color: widget.color,
                value: (v) => this.format(v, widget),
                tick: (v) => compactNumber(v),
                hero: this.isHero(widget),
                horizontal: this.isHorizontal(widget),
                // About two fifths of the chart's width for names, at ~8px a letter.
                labelChars: Math.floor((el.parentElement ? el.parentElement.clientWidth : 400) / 20),
                onPoint: widget.drill ? (i) => this.drillPoint(widget, series[i]) : null,
            });
        };
    }

    buildRing(widget) {
        return (el) => {
            const series = widget.series || [];
            return ringConfig(readTokens(el), {
                titles: series.map((p) => p.label),
                values: series.map((p) => p.value),
                others: series.map((p) => Boolean(p.other)),
                color: widget.color,
                value: (v) => this.format(v, widget),
                onPoint: widget.drill ? (i) => this.drillPoint(widget, series[i]) : null,
            });
        };
    }

    /** Legend rows for a ring: colour slot, name, value and share. */
    ringLegend(widget) {
        const series = widget.series || [];
        const whole = series.reduce((sum, p) => sum + Math.max(0, p.value || 0), 0) || 1;
        let slot = widget.color || 0;
        return series.map((p, index) => ({
            key: `${index}`,
            label: p.other && p.count ? _t("Other (%s)", p.count) : p.label,
            value: this.format(p.value, widget, { compact: true }),
            share: `${Math.round((Math.max(0, p.value || 0) / whole) * 100)}%`,
            swatch: p.other ? "o_ai_sw--other" : `o_ai_sw-${((slot++) % SERIES_SLOTS) + 1}`,
            point: p,
        }));
    }

    kpiView(widget) {
        const change = widget.compare !== undefined
            ? describeChange(widget.value, widget.compare, widget.polarity) : null;
        let note = widget.window_label || "";
        if (change && change.isNew) {
            note = widget.compare_label ? _t("Nothing in %s", widget.compare_label) : _t("Nothing in the comparison period");
        } else if (change) {
            const was = this.format(widget.compare, widget, { compact: true });
            note = widget.compare_label ? _t("was %(was)s · %(when)s", { was, when: widget.compare_label }) : _t("was %s", was);
        }
        return {
            parts: this.parts(widget.value, widget),
            full: this.format(widget.value, widget),
            change,
            note,
            tone: change ? change.tone : "neutral",
        };
    }

    pnlView(widget) {
        const largest = Math.max(Math.abs(widget.income || 0), Math.abs(widget.expenses || 0)) || 1;
        return {
            net: this.parts(widget.value, widget),
            loss: (widget.value || 0) < 0,
            income: this.parts(widget.income || 0, widget),
            expenses: this.parts(widget.expenses || 0, widget),
            incomeWidth: Math.round((Math.abs(widget.income || 0) / largest) * 100),
            expensesWidth: Math.round((Math.abs(widget.expenses || 0) / largest) * 100),
        };
    }

    /** Ageing as one bar of buckets, oldest darkest, and the list beneath. */
    agingView(widget) {
        const series = widget.series || [];
        const total = series.reduce((sum, p) => sum + (p.value || 0), 0);
        const overdue = total - ((series[0] && series[0].value) || 0);
        return {
            total: this.parts(total, widget),
            overdue: overdue > 0 ? this.format(overdue, widget, { compact: true }) : "",
            segments: series.map((p, index) => {
                const width = total ? ((p.value || 0) / total) * 100 : 0;
                return {
                    key: `${index}`,
                    width,
                    ramp: `o_ai_ramp-${Math.min(index + 1, 5)}`,
                    inside: width >= 14 ? `${Math.round(width)}%` : "",
                    title: `${p.label}: ${this.format(p.value, widget)}`,
                };
            }).filter((s) => s.width > 0),
            rows: series.map((p, index) => ({
                key: `${index}`,
                label: p.label,
                value: this.format(p.value, widget),
                ramp: `o_ai_ramp-${Math.min(index + 1, 5)}`,
            })),
        };
    }

    tableCell(widget, value, index) {
        if (index < (widget.numeric_from ?? 0)) {
            return value;
        }
        const key = (widget.column_keys || [])[index];
        return key === "__count" ? formatFloat(value || 0, { digits: [0, 0] }) : this.format(value, widget);
    }

    isNumericColumn(widget, index) {
        return index >= (widget.numeric_from ?? Infinity);
    }

    // ------------------------------------------------------------ pivots
    async pageAxis(widgetId, axis, direction) {
        const current = this.state.offsets[widgetId] || { row: 0, col: 0 };
        const widget = this.widgets.find((w) => w.id === widgetId);
        const pivot = widget && widget.pivot;
        if (!pivot) {
            return;
        }
        const cap = pivot[axis === "row" ? "rows" : "cols"].cap;
        const next = Math.max(0, (current[axis] || 0) + direction * cap);
        this.state.offsets = { ...this.state.offsets, [widgetId]: { ...current, [axis]: next } };
        await this.load();
    }

    /** "1–50 of 2,143": never a bare count that hides the rest. */
    axisRange(axis) {
        const from = axis.values.length ? axis.offset + 1 : 0;
        const to = axis.offset + axis.values.length;
        if (axis.total == null) {
            return _t("%(from)s–%(to)s", { from, to });
        }
        return _t("%(from)s–%(to)s of %(total)s", {
            from, to, total: formatFloat(axis.total, { digits: [0, 0] }),
        });
    }

    cell(pivot, rowKey, colKey) {
        return pivot.cells[`${rowKey}|${colKey}`];
    }

    // ------------------------------------------------------------- drill
    drillPoint(widget, point) {
        if (!point || point.other) {
            return;
        }
        this.drill(widget, point.raw);
    }

    async drill(widget, rawValue) {
        if (!widget.drill) {
            return;
        }
        try {
            const act = await this.orm.call("ai.dashboard.render", "drill", [
                this.dashboardId, widget.id, rawValue ?? null, this.state.filters,
            ]);
            await this.action.doAction(act);
        } catch {
            this.notification.add(_t("The records behind this tile could not be opened."), { type: "warning" });
        }
    }

    async drillCell(widget, row, col) {
        if (!widget.drill) {
            return;
        }
        try {
            const act = await this.orm.call("ai.dashboard.render", "drillCell", [
                this.dashboardId, widget.id,
                row.raw === undefined ? null : row.raw,
                col.raw === undefined ? null : col.raw,
                this.state.filters,
            ]);
            await this.action.doAction(act);
        } catch {
            this.notification.add(_t("The records behind this cell could not be opened."), { type: "warning" });
        }
    }

    // ------------------------------------------------------------ editing
    /**
     * Apply a change to the spec on screen, through the form's own record, so
     * discard, the unsaved-changes guard and Save behave as on any field.
     */
    async updateSpec(mutate) {
        const spec = this.spec;
        mutate(spec);
        this.state.pending = true;
        await this.record.update({ [this.props.name]: JSON.stringify(spec) });
    }

    async patchWidget(id, mutate) {
        await this.updateSpec((spec) => {
            const widget = (spec.widgets || []).find((w) => w.id === id);
            if (widget) {
                mutate(widget);
            }
        });
    }

    async patchQuery(id, mutate) {
        await this.updateSpec((spec) => {
            const widget = (spec.widgets || []).find((w) => w.id === id);
            if (widget && widget.query) {
                mutate(widget.query);
            }
        });
        await this.load();
    }

    async rename(id, title) {
        const clean = (title || "").trim();
        this.state.editing = null;
        if (clean) {
            await this.patchWidget(id, (w) => (w.title = clean));
        }
    }

    startRename(id) {
        if (this.showTools) {
            this.state.editing = id;
        }
    }

    onRenameKey(ev, id) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.rename(id, ev.target.value);
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            this.state.editing = null;
        }
    }

    onRenameBlur(ev, id) {
        if (this.state.editing === id) {
            this.rename(id, ev.target.value);
        }
    }

    async moveAfter(id, afterId) {
        await this.updateSpec((spec) => {
            const widgets = spec.widgets || [];
            const from = widgets.findIndex((w) => w.id === id);
            if (from < 0) {
                return;
            }
            const [moved] = widgets.splice(from, 1);
            const after = afterId ? widgets.findIndex((w) => w.id === afterId) : -1;
            widgets.splice(after + 1, 0, moved);
        });
    }

    async duplicate(id) {
        await this.updateSpec((spec) => {
            const widgets = spec.widgets || [];
            const index = widgets.findIndex((w) => w.id === id);
            if (index < 0) {
                return;
            }
            const copy = JSON.parse(JSON.stringify(widgets[index]));
            const taken = new Set(widgets.map((w) => w.id));
            let suffix = 2;
            while (taken.has(`${id}_${suffix}`)) {
                suffix++;
            }
            copy.id = `${id}_${suffix}`;
            copy.title = _t("%s (copy)", copy.title || "");
            widgets.splice(index + 1, 0, copy);
        });
    }

    async remove(id) {
        if ((this.spec.widgets || []).length <= 1) {
            this.notification.add(_t("A dashboard needs at least one tile. Delete the whole dashboard instead."), { type: "warning" });
            return;
        }
        await this.updateSpec((s) => {
            s.widgets = (s.widgets || []).filter((w) => w.id !== id);
        });
    }

    /** What a tile may safely become, decided by the shape of its query. */
    switchableTypes(widget) {
        if (widget.type === "kpi" || widget.group_count === 0) {
            return [];
        }
        if (widget.group_count === 1) {
            return [...CHART_TYPES, "table"];
        }
        if (widget.group_count === 2) {
            return ["table", "pivot"];
        }
        return ["table"];
    }

    granularityOf(widget) {
        const first = ((widget.query || {}).group_by || [])[0] || "";
        const parts = String(first).split(":");
        return parts.length > 1 && GRANULARITIES.includes(parts[1]) ? parts[1] : null;
    }

    async nextColor(widget) {
        await this.patchWidget(widget.id, (w) => (w.color = ((w.color || 0) + 1) % SERIES_SLOTS));
    }

    swatchClass(widget) {
        return `o_ai_sw-${((widget.color || 0) % SERIES_SLOTS) + 1}`;
    }

    /** Everything you do to a tile once, rather than while arranging it. */
    tileMenu(widget) {
        const items = [
            { label: _t("Rename…"), onSelected: () => this.startRename(widget.id) },
            { label: _t("Duplicate this tile"), onSelected: () => this.duplicate(widget.id) },
        ];
        for (const [span, label] of SIZES) {
            if (span !== widget.span) {
                items.push({ label: _t("Size: %s", label), onSelected: () => this.patchWidget(widget.id, (w) => (w.span = span)) });
            }
        }
        for (const type of this.switchableTypes(widget)) {
            if (type !== widget.type) {
                items.push({ label: _t("Show as %s", type), onSelected: () => this.patchWidget(widget.id, (w) => (w.type = type)) });
            }
        }
        const current = (widget.format && widget.format.kind) || "plain";
        if (widget.type !== "table") {
            for (const kind of FORMAT_KINDS) {
                if (kind !== current) {
                    items.push({
                        label: _t("Format as %s", FORMAT_LABELS[kind]),
                        onSelected: () => this.patchWidget(widget.id, (w) => (w.format = { ...(w.format || {}), kind })),
                    });
                }
            }
        }
        if (["kpi", "line", "bar"].includes(widget.type)) {
            const downIsGood = widget.polarity === "down_is_good";
            items.push({
                label: downIsGood ? _t("Treat a rise as good news") : _t("Treat a rise as bad news"),
                onSelected: () => this.patchWidget(widget.id, (w) => {
                    if (downIsGood) {
                        delete w.polarity;
                    } else {
                        w.polarity = "down_is_good";
                    }
                }),
            });
        }
        if (!["kpi", "pivot"].includes(widget.type) && widget.group_count >= 1) {
            for (const limit of ROW_LIMITS) {
                if (limit !== widget.query.limit) {
                    items.push({ label: _t("Show the top %s", limit), onSelected: () => this.patchQuery(widget.id, (q) => (q.limit = limit)) });
                }
            }
        }
        const granularity = this.granularityOf(widget);
        if (granularity) {
            for (const step of GRANULARITIES) {
                if (step !== granularity) {
                    items.push({
                        label: _t("Group %s", GRANULARITY_LABELS[step]),
                        onSelected: () => this.patchQuery(widget.id, (q) => {
                            q.group_by = (q.group_by || []).map((g, i) => (i === 0 ? `${g.split(":")[0]}:${step}` : g));
                        }),
                    });
                }
            }
        }
        items.push({
            label: widget.drill ? _t("Stop clicks opening records") : _t("Let clicks open the records"),
            onSelected: () => this.patchWidget(widget.id, (w) => (w.drill = w.drill === false)),
        });
        items.push({ label: _t("Remove this tile"), class: "text-danger", onSelected: () => this.remove(widget.id) });
        return items;
    }

    async saveEdits() {
        const saved = await this.record.save();
        if (saved) {
            this.state.pending = false;
            this.notification.add(_t("Your changes are saved."), { type: "success" });
        }
    }

    async discardEdits() {
        await this.record.discard();
        this.state.pending = false;
        await this.load();
    }

    get positionCards() {
        return POSITION_CARDS;
    }
}

export const aiDashboardCanvas = {
    component: AIDashboardCanvas,
    displayName: _t("Dashboard"),
    supportedTypes: ["text"],
};

registry.category("fields").add("ai_dashboard_canvas", aiDashboardCanvas);
