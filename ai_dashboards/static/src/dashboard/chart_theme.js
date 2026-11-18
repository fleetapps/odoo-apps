/** @odoo-module **/
/**
 * Chart.js configurations in the dashboard's look.
 *
 * Every option here is per chart. Nothing touches Chart.defaults or registers
 * a global plugin: Odoo's own graph view shares this copy of Chart.js
 * (web.chartjs_lib), and a dashboard must not restyle it.
 *
 * Colours are read from the page's CSS custom properties at draw time
 * (readTokens), so the light and dark bundles each get their own without a
 * branch in here.
 *
 * Mark specs: lines 2px with no resting markers and a soft wash beneath;
 * bars at most 24px thick with a 4px rounded data end and a square baseline
 * (Chart.js' default borderSkipped "start" keeps the baseline square);
 * hairline gridlines, no axis borders, at most five ticks.
 *
 * Reference: https://www.chartjs.org/docs/latest/ (Chart.js 4.4, as bundled by Odoo 19)
 */

const SERIES_SLOTS = 8;

function token(style, name, fallback) {
    const value = style.getPropertyValue(name).trim();
    return value || fallback;
}

/** The colours and font the page is drawn in, from its CSS tokens. */
export function readTokens(el) {
    const style = getComputedStyle(el);
    const series = [];
    for (let i = 1; i <= SERIES_SLOTS; i++) {
        series.push(token(style, `--ai-s${i}`, "#2a78d6"));
    }
    return {
        ink: token(style, "--ai-ink", "#1f2328"),
        muted: token(style, "--ai-muted", "#6b7178"),
        grid: token(style, "--ai-grid", "#f0f1f3"),
        axis: token(style, "--ai-axis", "#d3d6db"),
        surface: token(style, "--ai-surface", "#ffffff"),
        hair: token(style, "--ai-hair", "#eaecef"),
        other: token(style, "--ai-other", "#c3c7cc"),
        compare: token(style, "--ai-compare", "#b8bcc2"),
        series,
        font: style.fontFamily || "system-ui, sans-serif",
    };
}

/** A series colour by slot; the spec's 0-11 colour index maps onto 8 slots. */
export function seriesColor(tokens, index) {
    return tokens.series[(index || 0) % tokens.series.length];
}

/** #rrggbb or rgb() to rgba() with the given opacity. */
export function withAlpha(color, alpha) {
    const hex = /^#([0-9a-f]{6})$/i.exec(color);
    if (hex) {
        const n = parseInt(hex[1], 16);
        return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
    }
    const rgb = /^rgba?\(([^)]+)\)$/i.exec(color);
    if (rgb) {
        const [r, g, b] = rgb[1].split(",").map((part) => part.trim());
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
    return color;
}

/** A wash from the series colour to nothing, top to bottom of the plot. */
function wash(color, strength) {
    return (context) => {
        const { ctx, chartArea } = context.chart;
        if (!chartArea) {
            return withAlpha(color, 0);
        }
        const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        gradient.addColorStop(0, withAlpha(color, strength));
        gradient.addColorStop(1, withAlpha(color, 0));
        return gradient;
    };
}

/** A hairline following the pointer, so a line chart is read by date, not by aiming at a 2px stroke. */
const crosshair = {
    id: "aiCrosshair",
    afterDatasetsDraw(chart, _args, options) {
        const active = chart.tooltip && chart.tooltip.getActiveElements();
        if (!active || !active.length) {
            return;
        }
        const x = active[0].element.x;
        const { top, bottom } = chart.chartArea;
        const ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(x, top);
        ctx.lineTo(x, bottom);
        ctx.lineWidth = 1;
        ctx.strokeStyle = options.color;
        ctx.stroke();
        ctx.restore();
    },
};

function tooltip(tokens, { titles, value, lineKeys }) {
    return {
        backgroundColor: tokens.surface,
        titleColor: tokens.muted,
        bodyColor: tokens.ink,
        borderColor: tokens.hair,
        borderWidth: 1,
        cornerRadius: 8,
        padding: 10,
        caretSize: 0,
        displayColors: true,
        usePointStyle: true,
        boxWidth: 8,
        boxHeight: 8,
        boxPadding: 6,
        titleFont: { family: tokens.font, size: 12, weight: "normal" },
        bodyFont: { family: tokens.font, size: 13, weight: "600" },
        callbacks: {
            title: (items) => (items.length ? titles[items[0].dataIndex] : ""),
            label: (ctx) => ` ${value(ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed)}`,
            labelPointStyle: () => ({ pointStyle: lineKeys ? "line" : "rectRounded", rotation: 0 }),
        },
    };
}

function scales(tokens, { valueAxis, tick, showValueAxis = true, horizontal = false, labelChars }) {
    const font = { family: tokens.font, size: 11 };
    const category = {
        grid: { display: false },
        border: { display: false },
        ticks: { color: tokens.muted, font, maxRotation: 0, autoSkip: true, autoSkipPadding: 16 },
    };
    const values = {
        display: showValueAxis,
        beginAtZero: true,
        grid: { color: tokens.grid, drawTicks: false },
        border: { display: false },
        ticks: { color: tokens.muted, font, maxTicksLimit: 5, padding: 8, callback: tick },
    };
    if (!horizontal) {
        return { x: category, [valueAxis]: values };
    }
    // Long names are cut to fit their column rather than clipped at the
    // chart's edge; the tooltip still carries the whole name.
    const chars = Math.max(8, labelChars || 28);
    const ticks = {
        ...category.ticks,
        autoSkip: false,
        callback(value) {
            const label = String(this.getLabelForValue(value));
            return label.length > chars ? `${label.slice(0, chars - 1)}…` : label;
        },
    };
    return { x: values, y: { ...category, ticks } };
}

/**
 * A line or bar chart.
 *
 * @param {Object} tokens readTokens()
 * @param {Object} params
 * @param {"line"|"bar"} params.type
 * @param {string[]} params.labels axis labels
 * @param {string[]} params.titles tooltip titles (the long labels)
 * @param {number[]} params.values
 * @param {number[]|null} params.compare the comparison series, aligned
 * @param {string} params.compareLabel
 * @param {string} params.label the series' own name
 * @param {number} params.color the tile's colour index
 * @param {Function} params.value formats one value for a tooltip
 * @param {Function} params.tick formats an axis tick
 * @param {boolean} [params.hero] no value axis; the chart runs to the tile's edges
 * @param {boolean} [params.horizontal] bars laid out sideways, for long names
 * @param {Function} [params.onPoint] called with the clicked index
 */
export function cartesianConfig(tokens, params) {
    const color = seriesColor(tokens, params.color);
    const isLine = params.type === "line";
    const datasets = [];
    if (params.compare) {
        datasets.push({
            label: params.compareLabel,
            data: params.compare,
            borderColor: tokens.compare,
            backgroundColor: tokens.compare,
            borderWidth: isLine ? 1.5 : 0,
            pointRadius: 0,
            pointHoverRadius: isLine ? 3 : 0,
            cubicInterpolationMode: "monotone",
            fill: false,
            borderRadius: 4,
            maxBarThickness: 24,
            order: 2,
        });
    }
    datasets.push(isLine ? {
        label: params.label,
        data: params.values,
        borderColor: color,
        backgroundColor: wash(color, params.hero ? 0.22 : 0.16),
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBorderWidth: 2,
        pointHoverBorderColor: tokens.surface,
        pointHoverBackgroundColor: color,
        cubicInterpolationMode: "monotone",
        fill: "origin",
        order: 1,
    } : {
        label: params.label,
        data: params.values,
        backgroundColor: color,
        hoverBackgroundColor: withAlpha(color, 0.82),
        borderWidth: 0,
        borderRadius: 4,
        maxBarThickness: 24,
        categoryPercentage: 0.72,
        barPercentage: 0.86,
        order: 1,
    });
    return {
        type: params.type,
        data: { labels: params.labels, datasets },
        plugins: isLine ? [crosshair] : [],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 300 },
            indexAxis: params.horizontal ? "y" : "x",
            interaction: { mode: "index", intersect: false, axis: params.horizontal ? "y" : "x" },
            layout: { padding: params.hero ? { top: 8, left: 0, right: 0 } : { top: 4 } },
            plugins: {
                legend: { display: false },
                aiCrosshair: { color: tokens.axis },
                tooltip: tooltip(tokens, { titles: params.titles, value: params.value, lineKeys: isLine }),
            },
            scales: scales(tokens, {
                valueAxis: "y",
                tick: params.tick,
                showValueAxis: !params.hero,
                horizontal: params.horizontal,
                labelChars: params.labelChars,
            }),
            onClick: (_event, elements) => {
                const hit = elements.find((e) => e.datasetIndex === datasets.length - 1);
                if (hit && params.onPoint) {
                    params.onPoint(hit.index);
                }
            },
            onHover: (event, elements) => {
                const target = event.native && event.native.target;
                if (target) {
                    target.style.cursor = elements.length && params.onPoint ? "pointer" : "default";
                }
            },
        },
    };
}

/**
 * A donut: thin ring, a hairline of surface between slices, no legend on the
 * canvas (the tile draws its own beside it, with values and shares).
 */
export function ringColors(tokens, params) {
    return params.values.map((_v, i) =>
        params.others && params.others[i] ? tokens.other : seriesColor(tokens, (params.color || 0) + i));
}

export function ringConfig(tokens, params) {
    const colors = ringColors(tokens, params);
    return {
        type: "doughnut",
        data: {
            labels: params.titles,
            datasets: [{
                data: params.values.map((v) => Math.max(0, v)),
                backgroundColor: colors,
                hoverBackgroundColor: colors.map((c) => withAlpha(c, 0.82)),
                borderColor: tokens.surface,
                borderWidth: 2,
                hoverOffset: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: params.cutout || "72%",
            animation: { duration: 300 },
            plugins: {
                legend: { display: false },
                tooltip: tooltip(tokens, { titles: params.titles, value: params.value, lineKeys: false }),
            },
            onClick: (_event, elements) => {
                if (elements.length && params.onPoint) {
                    params.onPoint(elements[0].index);
                }
            },
        },
    };
}
