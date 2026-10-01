import { Component, onMounted, onWillStart, useEffect, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const PERIODS = [3, 7, 30];
const STORAGE_KEY = "odin_bar_dashboard";

function readPrefs() {
    try {
        return JSON.parse(browser.localStorage.getItem(STORAGE_KEY)) || {};
    } catch {
        return {};
    }
}

function writePrefs(prefs) {
    try {
        browser.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch {
        // Private window or storage blocked: the choice just is not remembered.
    }
}

/** 12400 → "12,400". */
export function money(value) {
    return Math.round(Number(value) || 0).toLocaleString("en-US");
}

/** 591400 → "591k", 12400 → "12.4k", 900 → "900". */
export function moneyShort(value) {
    const amount = Math.round(Number(value) || 0);
    if (Math.abs(amount) >= 100000) {
        return `${Math.round(amount / 1000)}k`;
    }
    if (Math.abs(amount) >= 10000) {
        return `${(Math.round(amount / 100) / 10).toLocaleString("en-US")}k`;
    }
    return amount.toLocaleString("en-US");
}

const CELL = {
    approved: { icon: "fa-check", tone: "o_done", text: "Approved" },
    submitted: { icon: "fa-check-circle-o", tone: "o_ready", text: "Counted, ready" },
    recount: { icon: "fa-repeat", tone: "o_attention", text: "Recount asked" },
    draft: { icon: "fa-adjust", tone: "o_attention", text: "Counting, not submitted" },
    none: { icon: "fa-minus", tone: "o_muted", text: "Not counted" },
    missed: { icon: "fa-times", tone: "o_critical", text: "Never closed" },
    before: { icon: "", tone: "o_blank", text: "Before the first count" },
    // The day the club is trading right now. Not a state anybody has to act
    // on -- it is shown so the grid does not appear to stop at yesterday.
    trading: { icon: "fa-circle-o", tone: "o_muted", text: "Trading now, closes tomorrow morning" },
};

/**
 * Bar Control dashboard, for managers, in order of what matters: is every
 * trading day closed (and what is left on the one to close next), how each
 * location is doing, what was lost and why, what to reorder, what left the
 * club. One location or all of them, over the last 3, 7 or 30 trading days.
 * Every figure opens the list behind it.
 */
export class BarDashboard extends Component {
    static template = "odin_bar_desk.BarDashboard";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const prefs = readPrefs();
        this.periods = PERIODS;
        this.money = money;
        this.moneyShort = moneyShort;
        this.state = useState({
            loading: true,
            data: null,
            error: "",
            period: PERIODS.includes(prefs.period) ? prefs.period : 7,
            barId: prefs.barId || false,
            varianceTab: "reason",
            openDay: null,
        });
        this.gridRef = useRef("grid");
        onWillStart(() => this.load());
        onMounted(() => this.scrollGridToEnd());
        useEffect(
            () => this.scrollGridToEnd(),
            () => [this.state.data]
        );
    }

    async load() {
        this.state.error = "";
        try {
            this.state.data = await this.orm.call("odin.bar.dashboard", "dashboard_data", [], {
                period: this.state.period,
                bar_id: this.state.barId || false,
            });
        } catch (error) {
            if (this.state.barId) {
                // The remembered location is gone: fall back to all of them.
                this.state.barId = false;
                this.savePrefs();
                return this.load();
            }
            this.state.error = error.data?.message || error.message || String(error);
        } finally {
            this.state.loading = false;
        }
    }

    /** The newest day sits on the right: keep it in view when the grid scrolls. */
    scrollGridToEnd() {
        const grid = this.gridRef.el;
        if (grid) {
            grid.scrollLeft = grid.scrollWidth;
        }
    }

    savePrefs() {
        writePrefs({ period: this.state.period, barId: this.state.barId });
    }

    async setPeriod(period) {
        if (period === this.state.period) {
            return;
        }
        this.state.period = period;
        this.savePrefs();
        await this.reload();
    }

    async setBar(barId) {
        if (barId === this.state.barId) {
            return;
        }
        this.state.barId = barId;
        this.state.openDay = null;
        this.savePrefs();
        await this.reload();
    }

    async reload() {
        this.state.loading = true;
        await this.load();
    }

    get data() {
        return this.state.data;
    }

    get barName() {
        return this.data.bars.find((bar) => bar.id === this.state.barId)?.name || "";
    }

    // ------------------------------------------------------------------
    // Closing grid
    // ------------------------------------------------------------------

    cellMeta(cell) {
        const meta = CELL[cell.state] || CELL.none;
        if ((cell.state === "submitted" || cell.state === "recount") && cell.unresolved) {
            return { ...meta, icon: "", number: cell.unresolved, tone: "o_attention", text: `${cell.unresolved} to explain` };
        }
        return meta;
    }

    cellTitle(row, cell, day) {
        const parts = [`${row.bar.name}, ${day.label}: ${this.cellMeta(cell).text}`];
        if (cell.pos_missing) {
            parts.push("POS sales not in");
        }
        return parts.join(" · ");
    }

    dayMeta(day) {
        return {
            approved: { icon: "fa-check", tone: "o_done", text: "Approved" },
            open: { icon: "fa-adjust", tone: "o_attention", text: "Open" },
            missed: { icon: "fa-times", tone: "o_critical", text: "Never closed" },
            before: { icon: "", tone: "o_blank", text: "" },
            trading: { icon: "fa-circle-o", tone: "o_muted", text: "Trading now, closes tomorrow morning" },
        }[day.state];
    }

    tapCell(cell, day) {
        if (day.state === "trading") {
            return;
        }
        if (day.in_reach && day.state !== "approved") {
            return this.openDesk(day.date);
        }
        if (cell.count_id) {
            return this.open("count", { record_id: cell.count_id });
        }
        return this.open("count", { date_from: day.date, date_to: day.date });
    }

    tapDay(day) {
        if (day.state === "before" || day.state === "trading") {
            return;
        }
        if (day.in_reach && day.state !== "approved") {
            return this.openDesk(day.date);
        }
        return this.open("count", { date_from: day.date, date_to: day.date, bar_id: false });
    }

    openDesk(date) {
        return this.open("desk", { date_from: date });
    }

    // ------------------------------------------------------------------
    // Locations, variance, stock
    // ------------------------------------------------------------------

    trendOf(now, before) {
        if (!before) {
            return null;
        }
        const pct = Math.round((100 * (now - before)) / before);
        if (!pct) {
            return { icon: "fa-arrows-h", text: "same as before", tone: "" };
        }
        return pct > 0
            ? { icon: "fa-arrow-up", text: `${pct}% more than the ${this.data.period} days before`, tone: "o_critical" }
            : { icon: "fa-arrow-down", text: `${-pct}% less than the ${this.data.period} days before`, tone: "o_done" };
    }

    lastCount(card) {
        const last = card.last_count;
        if (!last) {
            return { text: "Never counted", tone: "o_muted" };
        }
        if (last.state === "approved") {
            return { text: `${last.label} approved`, tone: "o_done" };
        }
        if (last.state === "draft") {
            return { text: `${last.label} counting`, tone: "o_attention" };
        }
        if (last.unresolved) {
            return { text: `${last.label}: ${last.unresolved} to explain`, tone: "o_attention" };
        }
        return { text: `${last.label} counted, to approve`, tone: "o_ready" };
    }

    width(value, rows) {
        const max = Math.max(...rows.map((row) => row.value), 0);
        return max && value > 0 ? `${Math.max(2, (100 * value) / max)}%` : "0%";
    }

    // ------------------------------------------------------------------
    // Opening what is behind a figure
    // ------------------------------------------------------------------

    async open(what, { bar_id = undefined, date_from = undefined, date_to = undefined, record_id = false } = {}) {
        const action = await this.orm.call("odin.bar.dashboard", "dashboard_open", [what], {
            bar_id: bar_id === undefined ? this.state.barId || false : bar_id,
            date_from: date_from === undefined ? this.data.date_from : date_from,
            date_to: date_to === undefined ? this.data.date_to : date_to,
            record_id,
        });
        await this.action.doAction(action);
    }
}

registry.category("actions").add("bar_dashboard", BarDashboard);
