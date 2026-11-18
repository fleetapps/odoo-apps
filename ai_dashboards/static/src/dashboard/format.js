/** @odoo-module **/
/**
 * Numbers as a dashboard shows them.
 *
 * Everything goes through Odoo's own formatters (@web/core/utils/numbers,
 * @web/core/currency) so the reader's thousands separator and decimal point
 * apply — the old canvas used toLocaleString(undefined), which follows the
 * browser rather than the language the person chose in Odoo.
 *
 * Big figures are shortened to about three significant digits (1.84M, 26.8M,
 * 183k) so a KPI never wraps mid-number; the full figure stays available for
 * a tooltip. The currency symbol is returned separately so the template can
 * draw it smaller and quieter than the number.
 */
import { getCurrency } from "@web/core/currency";
import { _t } from "@web/core/l10n/translation";
import { localization } from "@web/core/l10n/localization";
import { formatFloat, humanNumber } from "@web/core/utils/numbers";

/** Figures from this size up are shortened on tiles. */
export const COMPACT_FROM = 100000;

/** Changes beyond this are shown as a multiple ("89.6×"), not "+8,862%". */
const MULTIPLE_FROM = 999;

function trimZeros(text) {
    const point = localization.decimalPoint || ".";
    const at = text.lastIndexOf(point);
    if (at < 0) {
        return text;
    }
    const match = /^(\d*?)(0*)(\D*)$/.exec(text.slice(at + point.length));
    if (!match) {
        return text;
    }
    const [, digits, , rest] = match;
    return text.slice(0, at) + (digits ? point + digits : "") + rest;
}

/** About three significant digits once shortened: 1.84M, 26.8M, 183k. */
export function compactNumber(value) {
    const abs = Math.abs(value);
    if (abs < 1000) {
        return formatFloat(value, { digits: [0, abs < 10 && abs % 1 ? 1 : 0] });
    }
    const mantissa = abs / Math.pow(10, Math.floor(Math.log10(abs) / 3) * 3);
    const decimals = mantissa >= 100 ? 0 : mantissa >= 10 ? 1 : 2;
    return trimZeros(humanNumber(value, { decimals, minDigits: 1 }));
}

function decimalsFor(fmt, currency) {
    if (fmt.decimals !== undefined && fmt.decimals !== null) {
        return fmt.decimals;
    }
    if (fmt.kind === "monetary") {
        return currency && currency.decimals !== undefined ? currency.decimals : 2;
    }
    if (fmt.kind === "integer") {
        return 0;
    }
    return fmt.kind === "percent" ? 1 : 2;
}

/**
 * A value, split into its parts.
 *
 * @param {number|null|undefined} value
 * @param {Object} fmt the tile's format: {kind, decimals, suffix}
 * @param {Object} currency the render payload's currency: {id, symbol, position, decimals}
 * @param {Object} [options]
 * @param {boolean} [options.compact] shorten from COMPACT_FROM up
 * @returns {{symbol: string, before: boolean, number: string, suffix: string, text: string}}
 */
export function formatParts(value, fmt = {}, currency = {}, { compact = false } = {}) {
    if (value === null || value === undefined || Number.isNaN(value)) {
        return { symbol: "", before: true, number: "—", suffix: "", text: "—" };
    }
    const kind = fmt.kind || "plain";
    const shorten = compact && Math.abs(value) >= COMPACT_FROM && kind !== "percent";
    const decimals = decimalsFor(fmt, currency);
    let number;
    if (shorten) {
        number = compactNumber(value);
    } else if (kind === "plain" || kind === "duration") {
        // A plain figure only shows the decimals it has: 282, not 282.00.
        number = trimZeros(formatFloat(value, { digits: [0, decimals] }));
    } else {
        number = formatFloat(value, { digits: [0, decimals] });
    }
    let symbol = "";
    let before = true;
    if (kind === "monetary") {
        const known = (currency && currency.id && getCurrency(currency.id)) || {};
        symbol = known.symbol || (currency && currency.symbol) || "";
        before = (known.position || (currency && currency.position)) !== "after";
    }
    const suffix = kind === "percent" ? "%" : fmt.suffix || "";
    const text = symbol
        ? before ? `${symbol} ${number}${suffix}` : `${number}${suffix} ${symbol}`
        : `${number}${suffix}`;
    return { symbol, before, number, suffix, text };
}

/** The same, as one string — for axes, tooltips and highlights. */
export function formatValue(value, fmt, currency, options) {
    return formatParts(value, fmt, currency, options).text;
}

/**
 * How a figure moved against its comparison, and whether that is good news.
 *
 * Colour is direction × polarity: revenue up is good, costs up are not.
 * Against a zero there is no percentage to give, so the change is "New".
 *
 * @returns {null|{text: string, direction: "up"|"down"|"flat", tone: "good"|"bad"|"neutral", isNew: boolean}}
 */
export function describeChange(current, previous, polarity = "up_is_good") {
    if (previous === null || previous === undefined) {
        return null;
    }
    const now = current || 0;
    if (!previous) {
        return now ? { text: _t("New"), direction: "flat", tone: "neutral", isNew: true } : null;
    }
    const pct = ((now - previous) / Math.abs(previous)) * 100;
    if (!isFinite(pct)) {
        return null;
    }
    const direction = now > previous ? "up" : now < previous ? "down" : "flat";
    let text;
    const ratio = now / previous;
    if (Math.abs(pct) > MULTIPLE_FROM && ratio > 0) {
        text = `${formatFloat(ratio, { digits: [0, 1] })}×`;
    } else {
        text = `${formatFloat(Math.abs(pct), { digits: [0, 1] })}%`;
    }
    let tone = "neutral";
    if (direction !== "flat") {
        const good = (direction === "up") === (polarity !== "down_is_good");
        tone = good ? "good" : "bad";
    }
    return { text, direction, tone, isNew: false };
}

/**
 * Labels for a chart axis: the short form ("Mar") when every point is a date
 * in the same year, the long form ("Mar 2026") otherwise.
 */
export function axisLabels(series) {
    const years = new Set();
    for (const point of series) {
        const raw = point.raw;
        if (typeof raw !== "string" || !/^\d{4}-\d{2}-\d{2}/.test(raw)) {
            return series.map((p) => p.label);
        }
        years.add(raw.slice(0, 4));
    }
    return years.size === 1 ? series.map((p) => p.short || p.label) : series.map((p) => p.label);
}
