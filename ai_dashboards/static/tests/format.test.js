/** @odoo-module */
/**
 * Unit tests (Hoot) for the dashboard's number words and highlights.
 * Run in the browser at /web/tests?filter=AI%20Dashboards
 */
import { describe, expect, test } from "@odoo/hoot";
import { makeMockEnv, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { localization } from "@web/core/l10n/localization";

import {
    axisLabels,
    compactNumber,
    describeChange,
    formatParts,
} from "@ai_dashboards/dashboard/format";
import { computeHighlights } from "@ai_dashboards/dashboard/highlights";

const KES = { symbol: "KSh", position: "before", decimals: 2 };
const money = { kind: "monetary", decimals: 0 };

describe.current.tags("headless");

// The formatters read the reader's language: start a mock environment (which
// starts Odoo's localization and translation services), then pin the
// separators so the expectations do not depend on the test machine.
async function useEnglishNumbers() {
    await makeMockEnv();
    patchWithCleanup(localization, { decimalPoint: ".", grouping: [3, 0], thousandsSep: "," });
}

describe("AI Dashboards: numbers", () => {
    test("big figures are shortened to about three significant digits", async () => {
        await useEnglishNumbers();
        expect(compactNumber(1843668.6)).toBe("1.84M");
        expect(compactNumber(26822948.87)).toBe("26.8M");
        expect(compactNumber(182969.66)).toBe("183k");
        expect(compactNumber(-5633517.93)).toBe("-5.63M");
        expect(compactNumber(1800000)).toBe("1.8M");
        expect(compactNumber(999)).toBe("999");
    });

    test("money keeps its symbol apart, and shortens only from 100k", async () => {
        await useEnglishNumbers();
        const small = formatParts(9112.07, money, KES, { compact: true });
        expect(small.symbol).toBe("KSh");
        expect(small.before).toBe(true);
        expect(small.number).toBe("9,112");
        const big = formatParts(1823323.6, money, KES, { compact: true });
        expect(big.number).toBe("1.82M");
        const full = formatParts(1823323.6, money, KES);
        expect(full.number).toBe("1,823,324");
    });

    test("a plain figure shows only the decimals it has", async () => {
        await useEnglishNumbers();
        expect(formatParts(282, { kind: "plain" }).number).toBe("282");
        expect(formatParts(2.5, { kind: "plain" }).number).toBe("2.5");
        expect(formatParts(12.5, { kind: "percent" }).text).toBe("12.5%");
        expect(formatParts(null, { kind: "plain" }).text).toBe("—");
    });
});

describe("AI Dashboards: changes", () => {
    test("direction and polarity decide the colour", async () => {
        await useEnglishNumbers();
        expect(describeChange(110, 100)).toEqual({ text: "10.0%", direction: "up", tone: "good", isNew: false });
        expect(describeChange(110, 100, "down_is_good").tone).toBe("bad");
        expect(describeChange(90, 100, "down_is_good").tone).toBe("good");
        expect(describeChange(100, 100).tone).toBe("neutral");
    });

    test("a huge change reads as a multiple, a change from nothing as new", async () => {
        await useEnglishNumbers();
        expect(describeChange(1823323.6, 20345).text).toBe("89.6×");
        expect(describeChange(9112, 0)).toEqual({ text: "New", direction: "flat", tone: "neutral", isNew: true });
        expect(describeChange(0, 0)).toBe(null);
        expect(describeChange(5, undefined)).toBe(null);
    });

    test("an axis inside one year drops the year", async () => {
        await useEnglishNumbers();
        const sameYear = [
            { raw: "2026-01-01", label: "Jan 2026", short: "Jan" },
            { raw: "2026-02-01", label: "Feb 2026", short: "Feb" },
        ];
        expect(axisLabels(sameYear)).toEqual(["Jan", "Feb"]);
        const twoYears = [
            { raw: "2025-12-01", label: "Dec 2025", short: "Dec" },
            { raw: "2026-01-01", label: "Jan 2026", short: "Jan" },
        ];
        expect(axisLabels(twoYears)).toEqual(["Dec 2025", "Jan 2026"]);
        expect(axisLabels([{ raw: 4, label: "Naivas" }])).toEqual(["Naivas"]);
    });
});

describe("AI Dashboards: highlights", () => {
    test("overdue, movement and concentration, in that order", async () => {
        await useEnglishNumbers();
        const widgets = [
            { type: "aging", title: "Bills", format: money,
              series: [{ value: 134483 }, { value: 562801 }, { value: 762716 }] },
            { type: "kpi", title: "Sales", format: money, value: 1823323.6, compare: 20345,
              compare_label: "Oct 2026", polarity: "up_is_good" },
            { type: "bar", title: "By customer", format: money,
              series: [{ label: "Chandarana", value: 1032966, raw: 311 },
                       { label: "Naivas", value: 182969, raw: 50 },
                       { label: "Eastmatt", value: 120302, raw: 281 }] },
        ];
        const found = computeHighlights(widgets, KES).map(String);
        expect(found.length).toBe(3);
        expect(found[0]).toInclude("overdue");
        expect(found[0]).toInclude("91%");
        expect(found[1]).toInclude("89.6×");
        expect(found[1]).toInclude("Oct 2026");
        expect(found[2]).toInclude("Chandarana");
        expect(found[2]).toInclude("77%");
    });

    test("names are escaped before they are put in bold", async () => {
        await useEnglishNumbers();
        const widgets = [{
            type: "donut", title: "Spend", format: money,
            series: [{ label: "<img src=x onerror=alert(1)>", value: 900, raw: 1 },
                     { label: "B", value: 50, raw: 2 }, { label: "C", value: 50, raw: 3 }],
        }];
        const html = String(computeHighlights(widgets, KES)[0]);
        expect(html).not.toInclude("<img");
        expect(html).toInclude("&lt;img");
    });

    test("a date axis is a trend, not a share", async () => {
        await useEnglishNumbers();
        const widgets = [{
            type: "bar", title: "By month", format: money,
            series: [{ label: "Jan", value: 900, raw: "2026-01-01" },
                     { label: "Feb", value: 50, raw: "2026-02-01" },
                     { label: "Mar", value: 50, raw: "2026-03-01" }],
        }];
        expect(computeHighlights(widgets, KES)).toEqual([]);
    });
});
