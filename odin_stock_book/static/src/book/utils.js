// Shared helpers, the same as Petty Cash Book: amounts and days typed the quick way.
/** "1,500" or "1500.50" → number; `fallback` when it is not a number. */
export function parseAmount(value, fallback = 0) {
    if (value === "" || value === null || value === undefined) {
        return fallback === 0 ? 0 : fallback;
    }
    const n = Number(String(value).replace(/[,\s]/g, ""));
    return Number.isNaN(n) ? fallback : n;
}

const MONTH_KEYS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/**
 * A day typed the quick way, Kenyan order: "3" (this month), "3/11",
 * "3/11/26", "3 nov", or an ISO date. Returns "YYYY-MM-DD" or null.
 */
export function parseDay(text, year, month) {
    const value = String(text || "").trim().toLowerCase();
    if (!value) {
        return null;
    }
    let d, m = month, y = year, match;
    if ((match = value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/))) {
        [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])];
    } else if ((match = value.match(/^(\d{1,2})$/))) {
        d = Number(match[1]);
    } else if ((match = value.match(/^(\d{1,2})[/.\- ](\d{1,2})(?:[/.\- ](\d{2,4}))?$/))) {
        d = Number(match[1]);
        m = Number(match[2]);
        y = match[3] ? Number(match[3].length === 2 ? "20" + match[3] : match[3]) : year;
    } else if ((match = value.match(/^(\d{1,2})\s*([a-z]{3})[a-z]*\.?(?:\s+(\d{2,4}))?$/))) {
        d = Number(match[1]);
        m = MONTH_KEYS.indexOf(match[2]) + 1;
        y = match[3] ? Number(match[3].length === 2 ? "20" + match[3] : match[3]) : year;
    } else {
        return null;
    }
    const date = new Date(Date.UTC(y, m - 1, d));
    if (!m || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d) {
        return null;
    }
    return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

