/** @odoo-module **/

import { Component, onWillStart, onWillUnmount, useRef, useState, useEffect } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
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
const FORMAT_KINDS = ["plain", "integer", "monetary", "percent"];
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
            editing: null,   // widget id currently being renamed
        });

        // One Chart instance per canvas element, torn down explicitly: Chart.js
        // keeps its own registry keyed on the canvas, and leaking them is how a
        // dashboard gets slower every time you reopen it.
        this.charts = new Map();

        onWillStart(async () => {
            await loadBundle(CHART_BUNDLE);
            await this.load();
        });

        // Redraw whenever the rendered data changes. Chart.js needs a live
        // canvas element, so this has to run after the DOM exists.
        useEffect(
            () => {
                this.drawAll();
                return () => this.destroyCharts();
            },
            () => [this.state.data]
        );

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
                [this.dashboardId, this.state.filters]
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
        for (const widget of this.state.data.widgets) {
            if (!CHART_TYPES.includes(widget.type) || widget.error) {
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

        return {
            type: widget.type === "donut" ? "doughnut" : widget.type,
            data: {
                labels,
                datasets: [
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
                    legend: { display: isCircular, position: "right" },
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

    /** The trend against the comparison period, as a signed percentage. */
    delta(widget) {
        if (widget.compare == null || !widget.compare) {
            return null;
        }
        const previous = widget.compare;
        if (!previous) {
            return null;
        }
        const change = ((widget.value - previous) / Math.abs(previous)) * 100;
        if (!isFinite(change)) {
            return null;
        }
        return {
            value: `${change > 0 ? "+" : ""}${change.toFixed(1)}%`,
            up: change >= 0,
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

    // -------------------------------------------------------------- editing
    /** Write the spec back through the form's own record, so the usual
     *  dirty-state, discard and save machinery keeps working. */
    async updateSpec(mutate) {
        const spec = this.spec;
        mutate(spec);
        await this.props.record.update({ [this.props.name]: JSON.stringify(spec) });
    }

    widgetInSpec(spec, id) {
        return (spec.widgets || []).find((w) => w.id === id);
    }

    async rename(id, title) {
        const clean = (title || "").trim();
        if (!clean) {
            return;
        }
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget) {
                widget.title = clean;
            }
        });
        this.state.editing = null;
        await this.load();
    }

    async resize(id, delta) {
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget) {
                widget.span = Math.max(1, Math.min(12, (widget.span || 6) + delta));
            }
        });
        await this.load();
    }

    async setType(id, type) {
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget) {
                widget.type = type;
            }
        });
        await this.load();
    }

    async setFormat(id, kind) {
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget) {
                widget.format = { ...(widget.format || {}), kind };
            }
        });
        await this.load();
    }

    async toggleDrill(id) {
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget) {
                widget.drill = widget.drill === false;
            }
        });
        await this.load();
    }

    async setColor(id, color) {
        await this.updateSpec((spec) => {
            const widget = this.widgetInSpec(spec, id);
            if (widget) {
                widget.color = color;
            }
        });
        await this.load();
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
        await this.load();
    }

    async remove(id) {
        const spec = this.spec;
        if ((spec.widgets || []).length <= 1) {
            this.notification.add(
                _t("A dashboard needs at least one tile. Delete the whole dashboard instead."),
                { type: "warning" }
            );
            return;
        }
        await this.updateSpec((s) => {
            s.widgets = (s.widgets || []).filter((w) => w.id !== id);
        });
        await this.load();
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
        return ["table"];
    }

    get formatKinds() {
        return FORMAT_KINDS;
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
                onSelected: () => (this.state.editing = widget.id),
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
        items.push({
            label: widget.drill
                ? _t("Stop clicks opening records")
                : _t("Let clicks open the records"),
            onSelected: () => this.toggleDrill(widget.id),
        });
        items.push({
            label: _t("Next colour"),
            onSelected: () =>
                this.setColor(widget.id, ((widget.color || 0) + 1) % PALETTE.length),
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
