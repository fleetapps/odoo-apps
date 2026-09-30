/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useRef, useState, useEffect } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { useService } from "@web/core/utils/hooks";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { _t } from "@web/core/l10n/translation";

// Chart.js ships with Odoo but is lazy-loaded rather than sitting in
// assets_backend — the graph view pulls it the same way. Loading it here keeps
// the promise that this module adds no external dependency.
const CHART_BUNDLE = "web.chartjs_lib";

// Odoo's own kanban colour ramp, so a dashboard looks like the rest of the
// database rather than like a third-party widget bolted on.
const PALETTE = [
    "#5B8FF9", "#61DDAA", "#F6BD16", "#7262FD", "#78D3F8", "#9661BC",
    "#F6903D", "#008685", "#F08BB4", "#6DC8EC", "#269A99", "#D8584B",
];

const CHART_TYPES = ["bar", "line", "pie", "donut"];
// Which figures a tile needs. Two types in the same family are drawn from the
// same payload, so switching between them is instant and local; crossing
// families needs the server to shape the data differently. This one table is
// what lets the editor know when it may repaint on its own and when it has to
// ask — instead of asking every time, which is what made every edit feel like
// it had failed.
const SHAPE_FAMILY = {
    kpi: "kpi", table: "table", pivot: "pivot",
    bar: "chart", line: "chart", pie: "chart", donut: "chart",
};
// A comparison is a question you ask of a whole dashboard — "how does all of
// this look against last year?" — so it lives beside the period rather than
// per tile. A tile can still opt out in its spec.
const COMPARE_MODES = [
    ["none", "No comparison"],
    ["previous_period", "vs previous period"],
    ["previous_year", "vs last year"],
];
// Drawn in the same hue as the series it sits beside, at reduced strength, so
// which is "now" and which is "then" reads without consulting the legend.
const COMPARE_ALPHA = "44";
const FORMAT_KINDS = ["plain", "integer", "monetary", "percent"];
// "Show me the top ten" and "by quarter, not by month" are the two things
// people ask of a chart more than any other, and both used to mean going back
// to the assistant. They change the question rather than its appearance, so
// they are the only edits here that refetch.
const ROW_LIMITS = [5, 10, 20, 50];
const GRANULARITIES = ["day", "week", "month", "quarter", "year"];
const GRANULARITY_LABELS = {
    day: "by day", week: "by week", month: "by month",
    quarter: "by quarter", year: "by year",
};
// Named for a person, not for the schema: nobody reading a dashboard thinks
// "monetary".
const FORMAT_LABELS = {
    plain: "a plain number",
    integer: "a whole number",
    monetary: "money",
    percent: "a percentage",
};

export class AIDashboardCanvas extends Component {
    static template = "ai_dashboards.Canvas";
    static components = { Dropdown };
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.rootRef = useRef("root");

        this.state = useState({
            loading: true,
            error: null,
            data: null,
            filters: {},
            // Per-pivot paging, keyed by widget id. Kept in view state rather
            // than in the spec: which page you are looking at is not a
            // property of the dashboard, and paging must never dirty the
            // record or prompt anyone to save.
            offsets: {},
            editing: null,   // widget id currently being renamed
            // Set the moment the spec on screen stops matching the database.
            // Tracked explicitly rather than read off the record, because the
            // record is also dirtied by the name and description fields, and
            // those do not change a single figure.
            pending: false,
            // A saved dashboard is something you read. Arrows, handles and
            // menus on every tile turn reading into operating, so they stay
            // out of the way until you say you are editing.
            editMode: false,
        });

        // One Chart instance per canvas element, torn down explicitly: Chart.js
        // keeps its own registry keyed on the canvas, and leaking them is how a
        // dashboard gets slower every time you reopen it.
        this.charts = new Map();

        onWillStart(async () => {
            await loadBundle(CHART_BUNDLE);
            await this.load();
        });

        // Redraw when the figures change *or* when the spec does. A chart is
        // painted by Chart.js rather than by the template, so recolouring a
        // tile or switching a bar to a line changes nothing on screen unless
        // this runs — the second dependency is what makes those edits visible.
        useEffect(
            () => {
                this.drawAll();
                return () => this.destroyCharts();
            },
            () => [this.state.data, this.props.record.data[this.props.name]]
        );

        // Put the cursor in the title box the moment it opens, with the old
        // name selected, so renaming is one gesture instead of three.
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

        // One rule, and it replaces a pile of special cases: if the figures we
        // are holding cannot draw the spec that is now on screen, fetch new
        // ones. That covers a tile switched from a chart to a pivot, a tile the
        // assistant has just added, and a discard that puts back a spec we no
        // longer have the data for. Everything else — a title, a colour, a
        // width, a number format — is drawn from the spec directly and needs no
        // round trip at all.
        useRecordObserver(() => {
            if (this.staleFigures()) {
                this.load();
            }
        });

        onWillUnmount(() => this.destroyCharts());
    }

    // ------------------------------------------------------------- loading
    get dashboardId() {
        return this.props.record.resId;
    }

    get spec() {
        try {
            return JSON.parse(this.props.record.data[this.props.name] || "{}");
        } catch {
            return {};
        }
    }

    get isEditable() {
        return !this.props.readonly && this.state.data && this.state.data.is_owner;
    }

    /** Whether the tile controls are on screen.
     *
     *  Two separate questions, deliberately: *may* you edit this dashboard —
     *  which is about who owns it — and are you editing it *right now*.
     */
    get showTools() {
        return this.isEditable && this.state.editMode;
    }

    setEditMode(on) {
        this.state.editMode = on;
        if (!on) {
            this.state.editing = null;
        }
    }

    /** Edits made here and not yet written to the database.
     *
     *  Both halves matter. Our own flag alone would keep claiming there was
     *  something to save after the form's own Save button had already saved
     *  it; the record's dirty flag alone would claim it about a change to the
     *  dashboard's name, which this bar cannot save any differently.
     */
    get hasUnsavedEdits() {
        return this.state.pending && !!this.props.record.dirty;
    }

    /**
     * The tiles to draw: the spec decides what exists, in what order and how
     * it looks; the last render supplies the figures.
     *
     * This is the whole fix for "renaming a card does nothing". Presentation
     * used to be read from the render payload, which comes from the database —
     * so a rename updated the record, the canvas then redrew from the server,
     * and the old title came straight back. Reading it from the spec instead
     * means an edit appears the instant it is made, a discard puts the old one
     * back just as fast, and neither costs a round trip.
     */
    get widgets() {
        const drawn = new Map(
            ((this.state.data && this.state.data.widgets) || [])
                .map((w) => [w.id, w]));
        return (this.spec.widgets || []).map((w) => {
            const figures = drawn.get(w.id);
            const usable =
                figures && SHAPE_FAMILY[figures.type] === SHAPE_FAMILY[w.type];
            return {
                ...(usable ? figures : {}),
                id: w.id,
                // Everything below is what you see, and it comes from what is
                // on screen rather than from what is stored.
                type: w.type,
                title: w.title,
                span: Math.max(1, Math.min(12, w.span || 6)),
                color: w.color,
                format: w.format || { kind: "plain" },
                drill: w.drill !== false,
                // Computed here rather than taken from the payload so a tile
                // the assistant has only just added still knows what it may
                // become, before its first figures have arrived.
                group_count: ((w.query || {}).group_by || []).length,
                // Carried through so the tile menu can offer "top ten" and
                // "by quarter" without a round trip to find out the shape.
                query: w.query || {},
                // A tile whose figures do not match its type is honest about
                // it. The observer above is already fetching the right ones.
                calculating: !usable && !(figures && figures.error),
                error: (figures && figures.error) || undefined,
            };
        });
    }

    /** True when what we are holding cannot draw what is on screen. */
    staleFigures() {
        if (!this.state.data) {
            return false;
        }
        const drawn = new Map(this.state.data.widgets.map((w) => [w.id, w]));
        return (this.spec.widgets || []).some((w) => {
            const figures = drawn.get(w.id);
            return !figures ||
                SHAPE_FAMILY[figures.type] !== SHAPE_FAMILY[w.type];
        });
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            if (!this.dashboardId) {
                // An unsaved record has nothing to calculate against yet.
                this.state.data = null;
                return;
            }
            this.state.data = await this.orm.call(
                "ai.dashboard.render",
                "render",
                [this.dashboardId, this.state.filters, this.state.offsets,
                 // Draw what the person is looking at, not what happens to be
                 // stored. Without this, editing a tile into a shape the
                 // stored spec does not have redraws the stored one.
                 this.hasUnsavedEdits ? this.spec : null]
            );
        } catch (error) {
            this.state.error =
                (error && error.data && error.data.message) ||
                _t("This dashboard could not be calculated.");
        } finally {
            this.state.loading = false;
        }
    }

    async setFilter(key, value) {
        this.state.filters = { ...this.state.filters, [key]: value };
        await this.load();
    }

    /**
     * Page one axis of one pivot.
     *
     * Both axes move independently, because "the next fifty customers" and
     * "the next twelve months" are different questions and answering one
     * should not reset the other.
     */
    async pageAxis(widgetId, axis, direction) {
        const current = this.state.offsets[widgetId] || { row: 0, col: 0 };
        const pivot = this.pivotOf(widgetId);
        if (!pivot) {
            return;
        }
        const cap = pivot[axis === "row" ? "rows" : "cols"].cap;
        const next = Math.max(0, (current[axis] || 0) + direction * cap);
        this.state.offsets = {
            ...this.state.offsets,
            [widgetId]: { ...current, [axis]: next },
        };
        await this.load();
    }

    pivotOf(widgetId) {
        const widget = this.widgets.find(
            (w) => w.id === widgetId
        );
        return widget && widget.pivot;
    }

    /** "Showing 51–100 of 2,143" — never a bare count that hides the rest. */
    axisRange(axis) {
        const from = axis.values.length ? axis.offset + 1 : 0;
        const to = axis.offset + axis.values.length;
        if (axis.total == null) {
            return _t("Showing %(from)s–%(to)s", { from, to });
        }
        return _t("Showing %(from)s–%(to)s of %(total)s", {
            from,
            to,
            total: axis.total.toLocaleString(),
        });
    }

    cell(pivot, rowKey, colKey) {
        return pivot.cells[`${rowKey}|${colKey}`];
    }

    /** Whether any tile on this dashboard can carry a comparison at all. */
    get anyComparable() {
        return this.widgets.some((w) => w.comparable);
    }

    /** Whether any tile answers a click, so the footer does not promise one
     *  that nothing delivers. */
    get anyDrillable() {
        return this.widgets.some((w) => w.drill && !w.error);
    }

    get compareModes() {
        return COMPARE_MODES;
    }

    get compareMode() {
        return (this.state.data && this.state.data.compare) || "none";
    }

    /** Applies to every tile that can carry one, in one action. */
    async setCompare(mode) {
        await this.setFilter("__compare", mode);
    }

    // ------------------------------------------------------------- drawing
    destroyCharts() {
        for (const chart of this.charts.values()) {
            chart.destroy();
        }
        this.charts.clear();
    }

    drawAll() {
        this.destroyCharts();
        if (!this.state.data || !this.rootRef.el) {
            return;
        }
        for (const widget of this.widgets) {
            // `calculating` means the figures on hand were computed for a
            // different shape — handing those to Chart.js draws nonsense.
            if (!CHART_TYPES.includes(widget.type) || widget.error
                    || widget.calculating) {
                continue;
            }
            const canvas = this.rootRef.el.querySelector(
                `canvas[data-widget="${widget.id}"]`
            );
            if (canvas) {
                this.charts.set(widget.id, new Chart(canvas, this.config(widget)));
            }
        }
    }

    config(widget) {
        const series = widget.series || [];
        const labels = series.map((point) => point.label);
        const values = series.map((point) => point.value);
        const isCircular = widget.type === "pie" || widget.type === "donut";
        const base = widget.color != null ? widget.color : 0;

        const datasets = [];
        if (widget.compare_series) {
            // Behind the current series, not in front of it: the point of
            // comparison is to read *this* period against a backdrop.
            datasets.push({
                label: this.compareLabel,
                data: widget.compare_series,
                backgroundColor: PALETTE[base % PALETTE.length] + COMPARE_ALPHA,
                borderColor: PALETTE[base % PALETTE.length] + COMPARE_ALPHA,
                borderWidth: widget.type === "line" ? 2 : 0,
                borderDash: widget.type === "line" ? [4, 4] : undefined,
                fill: false,
                tension: 0.25,
            });
        }

        return {
            type: widget.type === "donut" ? "doughnut" : widget.type,
            data: {
                labels,
                datasets: [
                    ...datasets,
                    {
                        label: widget.title,
                        data: values,
                        backgroundColor: isCircular
                            ? series.map((_p, i) => PALETTE[(base + i) % PALETTE.length])
                            : PALETTE[base % PALETTE.length],
                        borderColor: PALETTE[base % PALETTE.length],
                        borderWidth: widget.type === "line" ? 2 : 0,
                        fill: false,
                        tension: 0.25,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        display: isCircular || Boolean(widget.compare_series),
                        position: isCircular ? "right" : "top",
                    },
                    tooltip: {
                        callbacks: {
                            label: (ctx) =>
                                `${ctx.label}: ${this.format(ctx.parsed.y ?? ctx.parsed, widget)}`,
                        },
                    },
                },
                scales: isCircular
                    ? {}
                    : {
                          y: {
                              beginAtZero: true,
                              ticks: { callback: (v) => this.format(v, widget) },
                          },
                      },
                onClick: (_event, elements) => {
                    if (!widget.drill || !elements.length) {
                        return;
                    }
                    const point = series[elements[0].index];
                    if (point) {
                        this.drill(widget, point.raw);
                    }
                },
            },
        };
    }

    // ------------------------------------------------------------ formatting
    format(value, widget) {
        if (value == null) {
            return "—";
        }
        const fmt = widget.format || { kind: "plain" };
        const currency = (this.state.data && this.state.data.currency) || {};
        const decimals =
            fmt.decimals != null
                ? fmt.decimals
                : fmt.kind === "monetary"
                ? currency.decimals ?? 2
                : fmt.kind === "integer"
                ? 0
                : 2;
        const number = Number(value).toLocaleString(undefined, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
        if (fmt.kind === "monetary" && currency.symbol) {
            return currency.position === "before"
                ? `${currency.symbol}${number}`
                : `${number}${currency.symbol}`;
        }
        if (fmt.kind === "percent") {
            return `${number}%`;
        }
        return fmt.suffix ? `${number}${fmt.suffix}` : number;
    }

    get compareLabel() {
        const found = COMPARE_MODES.find((m) => m[0] === this.compareMode);
        return found ? _t(found[1]) : _t("comparison");
    }

    /**
     * The trend against the comparison period.
     *
     * A percentage against a previous value of zero is infinity, which is
     * true and useless — so that case reports the movement in absolute terms
     * instead of hiding the tile's only interesting fact behind a dash.
     */
    delta(widget) {
        if (widget.compare == null) {
            return null;
        }
        const previous = widget.compare;
        const current = widget.value || 0;
        if (!previous) {
            if (!current) {
                return null;
            }
            return {
                value: this.format(current, widget),
                up: current > 0,
                absolute: true,
            };
        }
        const change = ((current - previous) / Math.abs(previous)) * 100;
        if (!isFinite(change)) {
            return null;
        }
        return {
            value: `${change > 0 ? "+" : ""}${change.toFixed(1)}%`,
            up: change >= 0,
            previous: this.format(previous, widget),
        };
    }

    // ------------------------------------------------------------ drilling
    async drill(widget, rawValue) {
        try {
            const act = await this.orm.call("ai.dashboard.render", "drill", [
                this.dashboardId,
                widget.id,
                rawValue ?? null,
                this.state.filters,
            ]);
            await this.action.doAction(act);
        } catch {
            this.notification.add(
                _t("The records behind this tile could not be opened."),
                { type: "warning" }
            );
        }
    }

    /**
     * Open the records behind one pivot cell.
     *
     * The most specific figure on any dashboard — one row value and one column
     * value — and until now the only one you could not get behind.
     */
    async drillCell(widget, row, col) {
        try {
            const act = await this.orm.call("ai.dashboard.render", "drillCell", [
                this.dashboardId,
                widget.id,
                row.raw === undefined ? null : row.raw,
                col.raw === undefined ? null : col.raw,
                this.state.filters,
            ]);
            await this.action.doAction(act);
        } catch {
            this.notification.add(
                _t("The records behind this cell could not be opened."),
                { type: "warning" }
            );
        }
    }

    // -------------------------------------------------------------- editing
    /**
     * Apply a change to the spec on screen.
     *
     * The change goes into the form's own record, so Odoo's discard, its
     * unsaved-changes guard and its Save button all keep working exactly as
     * they do on any other field. Nothing is written to the database here —
     * that is the Save button's job, and the bar at the top of the canvas
     * says so plainly.
     */
    async updateSpec(mutate) {
        const spec = this.spec;
        mutate(spec);
        this.state.pending = true;
        await this.props.record.update({ [this.props.name]: JSON.stringify(spec) });
    }

    widgetInSpec(spec, id) {
        return (spec.widgets || []).find((w) => w.id === id);
    }

    /** Change one tile in place. */
    async patchWidget(id, mutate) {
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget) {
                mutate(widget);
            }
        });
    }

    async rename(id, title) {
        const clean = (title || "").trim();
        this.state.editing = null;
        if (!clean) {
            // An empty title is a slip, not an instruction. Leave the old one.
            return;
        }
        await this.patchWidget(id, (w) => (w.title = clean));
    }

    async resize(id, delta) {
        await this.patchWidget(id, (w) => {
            w.span = Math.max(1, Math.min(12, (w.span || 6) + delta));
        });
    }

    async setType(id, type) {
        await this.patchWidget(id, (w) => (w.type = type));
    }

    async setFormat(id, kind) {
        await this.patchWidget(id, (w) => {
            w.format = { ...(w.format || {}), kind };
        });
    }

    async toggleDrill(id) {
        await this.patchWidget(id, (w) => (w.drill = w.drill === false));
    }

    async setColor(id, color) {
        await this.patchWidget(id, (w) => (w.color = color));
    }

    /**
     * Change the question a tile asks, then fetch the answer.
     *
     * The only edits in this file that need the server. Everything else is
     * presentation and is drawn straight from the spec.
     */
    async patchQuery(id, mutate) {
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget && widget.query) {
                mutate(widget.query);
            }
        });
        await this.load();
    }

    async setLimit(id, limit) {
        await this.patchQuery(id, (q) => {
            q.limit = limit;
        });
    }

    /** Regroup a date axis. Only the first grouping is touched: on a pivot
     *  that is the row axis, which is the one people mean. */
    async setGranularity(id, granularity) {
        await this.patchQuery(id, (q) => {
            const groups = q.group_by || [];
            if (groups.length) {
                q.group_by = groups.map((g, i) =>
                    i === 0 ? `${g.split(":")[0]}:${granularity}` : g);
            }
        });
    }

    /** The date granularity a tile is grouped by, or null if it is not a date
     *  axis at all. Only date fields take a granularity, so the colon is a
     *  reliable tell. */
    granularityOf(widget) {
        const first = ((widget.query || {}).group_by || [])[0] || "";
        const parts = String(first).split(":");
        return parts.length > 1 && GRANULARITIES.includes(parts[1])
            ? parts[1] : null;
    }

    /** Copy a tile, so "the same thing but as a table" or "the same chart
     *  filtered differently" starts from what is already right. */
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
            // Next to the original rather than at the end: you copied it to
            // compare the two.
            widgets.splice(index + 1, 0, copy);
        });
    }

    async move(id, direction) {
        await this.updateSpec((spec) => {
            const widgets = spec.widgets || [];
            const index = widgets.findIndex((w) => w.id === id);
            const target = index + direction;
            if (index < 0 || target < 0 || target >= widgets.length) {
                return;
            }
            [widgets[index], widgets[target]] = [widgets[target], widgets[index]];
        });
    }

    async remove(id) {
        if ((this.spec.widgets || []).length <= 1) {
            this.notification.add(
                _t("A dashboard needs at least one tile. Delete the whole dashboard instead."),
                { type: "warning" }
            );
            return;
        }
        await this.updateSpec((s) => {
            s.widgets = (s.widgets || []).filter((w) => w.id !== id);
        });
    }

    // ------------------------------------------------------ keeping changes
    /** Write the edits to the database, through the form's own record. */
    async saveEdits() {
        const saved = await this.props.record.save();
        if (saved) {
            this.state.pending = false;
            this.notification.add(_t("Your changes are saved."),
                                  { type: "success" });
        }
    }

    /** Put back the last saved version, and redraw from it. */
    async discardEdits() {
        await this.props.record.discard();
        this.state.pending = false;
        await this.load();
    }

    // ---------------------------------------------------------- the renamer
    /** Open the title for editing, with the old one selected so typing over
     *  it is one gesture rather than a select-all first. */
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
            // Escape means "forget it", so close without committing — and
            // without letting the blur that follows commit it anyway.
            this.state.editing = null;
            ev.target.blur();
        }
    }

    onRenameBlur(ev, id) {
        // Enter and Escape have both already closed the editor; this only
        // fires for clicking away, which means "keep what I typed".
        if (this.state.editing === id) {
            this.rename(id, ev.target.value);
        }
    }

    /**
     * What this tile may safely become, decided by the shape of its query
     * rather than by what it currently is.
     *
     * One grouping draws as any chart or as a table. Several groupings only
     * make sense as a table. None at all is a KPI and nothing else. Offering
     * a switch outside that would produce a spec the validator refuses — so
     * anything structural stays the AI's job, and the editor only offers
     * changes that are guaranteed to save.
     */
    switchableTypes(widget) {
        if (widget.type === "kpi" || widget.group_count === 0) {
            return [];
        }
        if (widget.group_count === 1) {
            return [...CHART_TYPES, "table"];
        }
        if (widget.group_count === 2) {
            // Exactly two groupings is the shape a pivot needs, so a table of
            // that shape can become one and back without touching the query.
            return ["table", "pivot"];
        }
        return ["table"];
    }

    get formatKinds() {
        return FORMAT_KINDS;
    }

    /** The colour this tile is currently drawn in. */
    paletteFor(widget) {
        return PALETTE[(widget.color || 0) % PALETTE.length];
    }

    async nextColor(widget) {
        await this.setColor(widget.id,
            ((widget.color || 0) + 1) % PALETTE.length);
    }

    /**
     * Everything you can do to a tile that is not moving or resizing it.
     *
     * A menu rather than a row of buttons: reordering and resizing are the two
     * things you do repeatedly while arranging a dashboard, so they stay under
     * the cursor. Changing a chart type or a number format happens once and
     * then never again, and fourteen buttons hovering over every tile turned
     * reading a dashboard into looking at a toolbar.
     */
    tileMenu(widget) {
        const items = [
            {
                label: _t("Rename…"),
                onSelected: () => this.startRename(widget.id),
            },
            {
                label: _t("Duplicate this tile"),
                onSelected: () => this.duplicate(widget.id),
            },
        ];
        for (const type of this.switchableTypes(widget)) {
            if (type === widget.type) {
                continue;
            }
            items.push({
                label: _t("Show as %s", type),
                onSelected: () => this.setType(widget.id, type),
            });
        }
        const current = (widget.format && widget.format.kind) || "plain";
        for (const kind of FORMAT_KINDS) {
            if (kind === current || widget.type === "table") {
                continue;
            }
            items.push({
                label: _t("Format as %s", FORMAT_LABELS[kind]),
                onSelected: () => this.setFormat(widget.id, kind),
            });
        }
        // How many rows. Meaningless without a grouping; a KPI is one number
        // by definition; and a pivot pages both of its axes with its own caps,
        // so a row limit there would fight the pager.
        if (!["kpi", "pivot"].includes(widget.type) && widget.group_count >= 1) {
            for (const limit of ROW_LIMITS) {
                if (limit === widget.query.limit) {
                    continue;
                }
                items.push({
                    label: _t("Show the top %s", limit),
                    onSelected: () => this.setLimit(widget.id, limit),
                });
            }
        }
        // Only offered where there is a date axis to regroup.
        const granularity = this.granularityOf(widget);
        if (granularity) {
            for (const step of GRANULARITIES) {
                if (step === granularity) {
                    continue;
                }
                items.push({
                    label: _t("Group %s", GRANULARITY_LABELS[step]),
                    onSelected: () => this.setGranularity(widget.id, step),
                });
            }
        }
        items.push({
            label: widget.drill
                ? _t("Stop clicks opening records")
                : _t("Let clicks open the records"),
            onSelected: () => this.toggleDrill(widget.id),
        });
        items.push({
            label: _t("Remove this tile"),
            class: "text-danger",
            onSelected: () => this.remove(widget.id),
        });
        return items;
    }

    get palette() {
        return PALETTE;
    }
}

export const aiDashboardCanvas = {
    component: AIDashboardCanvas,
    displayName: _t("Dashboard canvas"),
    supportedTypes: ["text"],
};

registry.category("fields").add("ai_dashboard_canvas", aiDashboardCanvas);
