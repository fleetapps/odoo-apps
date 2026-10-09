import { formatFloat } from "@web/core/utils/numbers";

/**
 * Figures as the P&L shows them: scaled (units, thousands, millions), with
 * the currency's decimals only when asked, negatives with a minus or in
 * parentheses. Grouping and separators follow the user's language through
 * formatFloat (@web/core/utils/numbers).
 */
export function formatAmount(value, { scale = 1, decimals = true, currencyDecimals = 2, parentheses = false } = {}) {
    if (value === null || value === undefined || value === false) {
        return "";
    }
    const scaled = value / scale;
    let digits = 0;
    if (scale === 1 && decimals) {
        digits = currencyDecimals;
    } else if (scale >= 1000000) {
        digits = 1;
    }
    const rounded = Math.abs(scaled) < 0.5 * Math.pow(10, -digits) ? 0 : scaled;
    const text = formatFloat(Math.abs(rounded), { digits: [69, digits] });
    if (rounded < 0) {
        return parentheses ? `(${text})` : `-${text}`;
    }
    return text;
}

/** 12.345 → "+12.3%", -4 → "-4.0%", 250 → "+250%". */
export function formatGrowth(value) {
    if (value === null || value === undefined) {
        return "";
    }
    const digits = Math.abs(value) < 100 ? 1 : 0;
    const text = formatFloat(Math.abs(value), { digits: [69, digits] });
    return `${value > 0 ? "+" : value < 0 ? "-" : ""}${text}%`;
}

export function formatPercent(value) {
    if (value === null || value === undefined) {
        return "";
    }
    return `${formatFloat(value, { digits: [69, Math.abs(value) < 10 ? 1 : 0] })}%`;
}

/** SVG polyline points for a 12-month sparkline. */
export function sparkPoints(values, width = 64, height = 18) {
    const numbers = (values || []).map((value) => Number(value) || 0);
    if (numbers.length < 2) {
        return "";
    }
    const min = Math.min(...numbers);
    const max = Math.max(...numbers);
    const span = max - min || 1;
    const step = width / (numbers.length - 1);
    return numbers
        .map((value, index) => {
            const x = (index * step).toFixed(1);
            const y = (height - 2 - ((value - min) / span) * (height - 4)).toFixed(1);
            return `${x},${y}`;
        })
        .join(" ");
}
