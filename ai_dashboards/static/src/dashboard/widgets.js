/** @odoo-module **/
/**
 * The two drawing components the dashboard page is built from.
 *
 * AiChart owns exactly one Chart.js instance for its lifetime and destroys it
 * when it changes or goes away: Chart.js keeps a registry keyed on the canvas,
 * and leaking instances is how a dashboard gets slower every time it opens.
 *
 * AiSparkline is plain SVG — a KPI's trend needs no axes, tooltips or library,
 * and nine of them on one page should cost nothing.
 */
import { Component, useEffect, useRef } from "@odoo/owl";

export class AiChart extends Component {
    static template = "ai_dashboards.Chart";
    static props = {
        // (canvasElement) => Chart.js config; called whenever `version` changes.
        build: Function,
        version: { optional: true },
        label: { type: String, optional: true },
        class: { type: String, optional: true },
        // Chart.js sizes itself to its box, so the box needs a height.
        height: { type: Number, optional: true },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        useEffect(
            () => {
                const el = this.canvasRef.el;
                // Chart is the global the web.chartjs_lib bundle defines.
                const chart = new Chart(el, this.props.build(el));
                return () => chart.destroy();
            },
            () => [this.props.version]
        );
    }
}

let sparkId = 0;

/** Monotone cubic (Fritsch–Carlson): smooth, and never dips below a zero it passes through. */
export function monotonePath(points) {
    const n = points.length;
    if (n < 2) {
        return "";
    }
    const dx = [];
    const slope = [];
    for (let i = 0; i < n - 1; i++) {
        dx[i] = points[i + 1][0] - points[i][0];
        slope[i] = (points[i + 1][1] - points[i][1]) / dx[i];
    }
    const t = [slope[0]];
    for (let i = 1; i < n - 1; i++) {
        t[i] = slope[i - 1] * slope[i] <= 0 ? 0 : (slope[i - 1] + slope[i]) / 2;
    }
    t[n - 1] = slope[n - 2];
    for (let i = 0; i < n - 1; i++) {
        if (slope[i] === 0) {
            t[i] = 0;
            t[i + 1] = 0;
            continue;
        }
        const a = t[i] / slope[i];
        const b = t[i + 1] / slope[i];
        const s = a * a + b * b;
        if (s > 9) {
            const q = 3 / Math.sqrt(s);
            t[i] = q * a * slope[i];
            t[i + 1] = q * b * slope[i];
        }
    }
    const f = (x) => Math.round(x * 100) / 100;
    let d = `M${f(points[0][0])} ${f(points[0][1])}`;
    for (let i = 0; i < n - 1; i++) {
        const h = dx[i] / 3;
        d += ` C${f(points[i][0] + h)} ${f(points[i][1] + t[i] * h)} ${f(points[i + 1][0] - h)} ${f(points[i + 1][1] - t[i + 1] * h)} ${f(points[i + 1][0])} ${f(points[i + 1][1])}`;
    }
    return d;
}

export class AiSparkline extends Component {
    static template = "ai_dashboards.Sparkline";
    static props = {
        values: Array,
        // good / bad / neutral: the trend is tinted like the change beside it.
        tone: { type: String, optional: true },
    };

    setup() {
        this.gradientId = `o_ai_spark_${++sparkId}`;
    }

    get paths() {
        const values = this.props.values.map((v) => Number(v) || 0);
        const n = values.length;
        const max = Math.max(...values);
        const min = Math.min(0, ...values);
        const span = max - min || 1;
        const points = values.map((v, i) => [(i / (n - 1)) * 100, 94 - ((v - min) / span) * 82]);
        const line = monotonePath(points);
        return { line, area: line ? `${line} L100 100 L0 100 Z` : "" };
    }
}
