/** @odoo-module **/
/**
 * Highlights: up to three sentences about what the tiles already show.
 *
 * Worked out here, in the browser, from the figures the page has just drawn —
 * by fixed rules, not by a language model — so a highlight can never say
 * anything the tiles do not. Each rule reads one kind of tile:
 *
 *   1. the KPI that moved most against its comparison;
 *   2. a bar or donut whose biggest item is 40% or more of the whole;
 *   3. an ageing card, by how much of it is overdue.
 *
 * Sentences are translated, and every figure or name in them is escaped
 * before it is put in bold: a customer's name is data, not markup.
 */
import { markup } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { escape } from "@web/core/utils/strings";
import { describeChange, formatValue } from "./format";

const CONCENTRATION = 0.4;
const MAX = 3;

function bold(text) {
    return markup(`<strong>${escape(String(text))}</strong>`);
}

function share(part, whole) {
    return `${Math.round((part / whole) * 100)}%`;
}

function movement(widgets, currency) {
    let best = null;
    for (const w of widgets) {
        if (w.type !== "kpi" || w.error || w.compare === null || w.compare === undefined || !w.compare) {
            continue;
        }
        const change = describeChange(w.value, w.compare, w.polarity);
        if (!change || change.direction === "flat") {
            continue;
        }
        const size = Math.abs(((w.value || 0) - w.compare) / w.compare);
        if (!best || size > best.size) {
            best = { w, change, size };
        }
    }
    if (!best) {
        return null;
    }
    const { w, change } = best;
    const value = formatValue(w.value, w.format, currency, { compact: true });
    const was = formatValue(w.compare, w.format, currency, { compact: true });
    const args = {
        title: w.title,
        value: bold(value),
        change: bold(change.text),
        against: w.compare_label || _t("the comparison"),
        was,
    };
    return change.direction === "up"
        ? _t("%(title)s is %(value)s, up %(change)s on %(against)s (%(was)s).", args)
        : _t("%(title)s is %(value)s, down %(change)s on %(against)s (%(was)s).", args);
}

function concentration(widgets, currency) {
    for (const w of widgets) {
        if (!["bar", "pie", "donut"].includes(w.type) || w.error || !w.series || w.series.length < 3) {
            continue;
        }
        if (w.series.some((p) => typeof p.raw === "string" && /^\d{4}-\d{2}/.test(p.raw))) {
            continue; // a date axis is a trend, not a share
        }
        const positive = w.series.filter((p) => p.value > 0 && !p.other);
        const whole = w.series.reduce((sum, p) => sum + Math.max(0, p.value), 0);
        if (!positive.length || !whole) {
            continue;
        }
        const top = positive.reduce((a, b) => (b.value > a.value ? b : a));
        if (top.value / whole >= CONCENTRATION) {
            return _t("%(name)s makes up %(share)s of %(title)s (%(value)s).", {
                name: bold(top.label),
                share: bold(share(top.value, whole)),
                title: w.title,
                value: formatValue(top.value, w.format, currency, { compact: true }),
            });
        }
    }
    return null;
}

function overdue(widgets, currency) {
    for (const w of widgets) {
        if (w.type !== "aging" || w.error || !w.series || w.series.length < 2) {
            continue;
        }
        const total = w.series.reduce((sum, p) => sum + (p.value || 0), 0);
        const late = total - (w.series[0].value || 0);
        if (late > 0 && total > 0) {
            return _t("%(value)s of %(title)s is overdue — %(share)s of the total.", {
                value: bold(formatValue(late, w.format, currency, { compact: true })),
                title: w.title,
                share: bold(share(late, total)),
            });
        }
    }
    return null;
}

/**
 * @param {Object[]} widgets the tiles as drawn (spec merged with figures)
 * @param {Object} currency the render payload's currency
 * @returns {Array} up to three translated, escaped sentences (Markup)
 */
export function computeHighlights(widgets, currency) {
    const found = [];
    for (const rule of [overdue, movement, concentration]) {
        const sentence = rule(widgets, currency);
        if (sentence) {
            found.push(sentence);
        }
        if (found.length >= MAX) {
            break;
        }
    }
    return found;
}
