/** @odoo-module **/
/**
 * Access Management dashboard - an OWL client action.
 *
 * All charts (donut, trend line, histograms, bar lists, score gauge, heatmap,
 * stacked composition bar) are drawn as inline SVG from geometry computed here,
 * so the dashboard has no charting dependency and renders identically on Odoo
 * 17, 18 and 19. Each chart carries the enterprise essentials: axis ticks,
 * gridlines, legends and native hover tooltips (<title>).
 */
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const PALETTE = [
    "#8d2b64", "#0f8f8f", "#3b82f6", "#22a06b", "#e0a417",
    "#e0533d", "#d6409f", "#7c5cd6", "#0ea5a5", "#64748b",
];
const MIX_COLORS = ["#7c5cd6", "#0f8f8f", "#3b82f6", "#d6409f", "#e0a417", "#64748b"];
const URGENCY = ["#e0533d", "#e0a417", "#0f8f8f", "#94a3b8"];

export class AccessDashboard extends Component {
    static template = "access_manager_pro.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.state = useState({
            loading: true,
            data: null,
            dark: false,
            sort: "most",
            heatCols: "core",
            heatModels: 10,
            selectedUser: false,
            inspection: null,
            inspectTab: "rules",
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call(
            "access.manager.profile", "get_dashboard_data", []);
        this.state.loading = false;
    }

    refresh() {
        return this.load();
    }
    toggleDark() {
        this.state.dark = !this.state.dark;
    }

    // ================= geometry helpers ================= //
    _niceTicks(max) {
        const m = Math.max(max, 1);
        return [0, Math.round(m / 2), m];
    }

    _donutGeom(items, colors) {
        // `realTotal` is what the chart *reports*; `total` is only the divisor
        // that keeps the arc maths finite when there is nothing to draw. Using
        // the fallback for both is what made an empty database read "1 rules".
        const realTotal = items.reduce((a, b) => a + b.value, 0);
        const total = realTotal || 1;
        const r = 54, C = 2 * Math.PI * r;
        let acc = 0;
        const arcs = items.map((it, i) => {
            const len = (it.value / total) * C;
            const seg = {
                dash: `${len.toFixed(2)} ${(C - len).toFixed(2)}`,
                off: -acc,
                color: colors[i % colors.length],
                label: it.label, value: it.value,
                pct: Math.round((it.value / total) * 100),
            };
            acc += len;
            return seg;
        });
        return { r, C, total: realTotal, arcs };
    }

    // vertical bars with y-axis + value labels; data = [{label,value}]
    _vBars(data, colors) {
        const w = 300, h = 140, padL = 26, padB = 24, padT = 12;
        const max = Math.max(...data.map((d) => d.value), 1);
        const innerW = w - padL - 8;
        const step = innerW / data.length;
        const bw = Math.min(38, step * 0.56);
        const y = (v) => (h - padB) - (v / max) * (h - padB - padT);
        const bars = data.map((d, i) => ({
            x: padL + step * i + (step - bw) / 2,
            y: y(d.value),
            w: bw,
            h: (h - padB) - y(d.value),
            cx: padL + step * i + step / 2,
            color: colors ? colors[i % colors.length] : "#7c5cd6",
            label: d.label, value: d.value,
        }));
        const ticks = this._niceTicks(max).map((v) => ({ v, y: y(v) }));
        return { w, h, padL, baseline: h - padB, bars, ticks };
    }

    // horizontal bars; data rows carry a model/company/name label -> pct + color
    _hBars(data, key) {
        const max = Math.max(...data.map((d) => d[key]), 1);
        return data.map((d, i) => ({
            ...d,
            label: d.model || d.company || d.name || d.label || "",
            pct: Math.max(4, (d[key] / max) * 100),
            color: PALETTE[i % PALETTE.length],
        }));
    }

    // ================= KPI cards ================= //
    get kpiCards() {
        const k = this.state.data.kpis;
        const p = (v) => `${v}% of all rules`;
        return [
            { label: "Total Rules", value: k.total, accent: "violet", icon: "fa-list-ul",
              sub: k.trend != null ? `${k.trend}x vs prior 30 days` : "all-time" },
            { label: "Active Rules", value: k.active, sub: p(k.active_pct), accent: "teal", icon: "fa-check-circle" },
            { label: "Inactive", value: k.inactive, sub: p(k.inactive_pct), accent: "slate", icon: "fa-pause-circle" },
            { label: "Expiring ≤ 7 days", value: k.expiring, sub: "action needed", accent: "amber", icon: "fa-exclamation-triangle" },
            { label: "Expired Today", value: k.expired_today, sub: "run cleanup", accent: "red", icon: "fa-calendar-times-o" },
            { label: "User Rules", value: k.user_rules, sub: p(k.user_pct), accent: "blue", icon: "fa-user" },
            { label: "Group Rules", value: k.group_rules, sub: p(k.group_pct), accent: "magenta", icon: "fa-users" },
            { label: "Time-Based", value: k.time_based, sub: p(k.time_pct), accent: "pink", icon: "fa-clock-o" },
            { label: "Login Disabled", value: k.login_disabled, sub: p(k.login_pct), accent: "dark", icon: "fa-lock" },
        ];
    }

    // ================= restriction-type donut ================= //
    get donut() {
        const s = this.state.data.restriction_split;
        return this._donutGeom(
            [{ label: "Group", value: s.group }, { label: "User", value: s.user }],
            ["#8d2b64", "#0f8f8f"]);
    }

    // ================= trend line (with axes + tooltips) ================= //
    get line() {
        const series = this.state.data.created_series;
        const w = 340, h = 148, padL = 26, padR = 10, padT = 12, padB = 24;
        const n = series.length;
        const maxY = Math.max(...series.map((p) => p.value), 4);
        const x = (i) => padL + (i * (w - padL - padR)) / (n - 1);
        const y = (v) => (h - padB) - (v / maxY) * (h - padB - padT);
        const pts = series.map((p, i) => ({ x: x(i), y: y(p.value), label: p.label, value: p.value }));
        const path = this._smooth(pts);
        const area = `${path} L ${pts[n - 1].x} ${h - padB} L ${pts[0].x} ${h - padB} Z`;
        const yTicks = this._niceTicks(maxY).map((v) => ({ v, y: y(v) }));
        return { w, h, padL, right: w - padR, pts, path, area, baseline: h - padB, yTicks,
                 last: pts[n - 1] };
    }
    _smooth(pts) {
        if (pts.length < 2) {
            return pts.length ? `M ${pts[0].x} ${pts[0].y}` : "";
        }
        let d = `M ${pts[0].x} ${pts[0].y}`;
        for (let i = 0; i < pts.length - 1; i++) {
            const a = pts[i], b = pts[i + 1];
            const mx = (a.x + b.x) / 2;
            d += ` C ${mx} ${a.y}, ${mx} ${b.y}, ${b.x} ${b.y}`;
        }
        return d;
    }

    // ================= restriction composition (stacked bar) ================= //
    get ruleMix() {
        const items = this.state.data.rule_mix;
        const realTotal = items.reduce((a, b) => a + b.value, 0);
        const total = realTotal || 1;   // divisor only - see _donutGeom
        return {
            total: realTotal,
            segments: items.map((it, i) => ({
                ...it, pct: (it.value / total) * 100,
                color: MIX_COLORS[i % MIX_COLORS.length],
            })).filter((s) => s.value > 0),
            legend: items.map((it, i) => ({
                ...it, color: MIX_COLORS[i % MIX_COLORS.length],
            })),
        };
    }

    // ================= new charts ================= //
    get loadHist() {
        return this._vBars(this.state.data.restriction_load, ["#7c5cd6", "#3b82f6", "#0f8f8f", "#e0a417"]);
    }
    get expBars() {
        return this._vBars(this.state.data.expirations, URGENCY);
    }
    get companyBars() {
        return this._hBars(this.state.data.by_company, "count");
    }
    get topUsers() {
        return this._hBars(this.state.data.top_users, "count");
    }

    // ================= restricted-model bars ================= //
    get bars() {
        const list = [...this.state.data.restricted_models];
        const s = this.state.sort;
        if (s === "most") list.sort((a, b) => b.count - a.count);
        else if (s === "least") list.sort((a, b) => a.count - b.count);
        else if (s === "az") list.sort((a, b) => a.model.localeCompare(b.model));
        else if (s === "za") list.sort((a, b) => b.model.localeCompare(a.model));
        return this._hBars(list, "count");
    }

    // ================= score gauge (colour by rating) ================= //
    get gauge() {
        const ins = this.state.data.insights;
        const r = 30, C = 2 * Math.PI * r;
        const len = (ins.config_score / 100) * C;
        const color = ins.config_score >= 80 ? "#17936a"
            : ins.config_score >= 60 ? "#c4881a" : "#e0533d";
        return { r, C, len, dash: `${len.toFixed(2)} ${(C - len).toFixed(2)}`,
                 score: ins.config_score, rating: ins.rating, color };
    }
    get insightRows() {
        const i = this.state.data.insights;
        return [
            { icon: "fa-shield", title: "Admin Protection", sub: "Administrators are never restricted",
              badge: "All admins excluded", tone: "good" },
            { icon: "fa-building-o", title: "Multi-Company Coverage", sub: "Companies with at least one active rule",
              badge: `${i.companies_covered} / ${i.companies_total} companies`, tone: "info" },
            { icon: "fa-refresh", title: "Cache Status", sub: "Rules engine cache",
              badge: i.cache_status, tone: "good" },
            { icon: "fa-cubes", title: "Models Covered", sub: "Distinct models touched by rules",
              badge: `${i.models_covered} models`, tone: "info" },
            { icon: "fa-user-circle-o", title: "Users Affected", sub: "Direct and via groups",
              badge: `${i.users_affected} users`, tone: "info" },
        ];
    }

    // ================= heatmap ================= //
    get heat() {
        const hm = this.state.data.heatmap;
        const core = ["read", "write", "create", "delete"];
        const cols = this.state.heatCols === "all" ? hm.actions : core;
        const rows = hm.models.slice(0, this.state.heatModels);
        return { cols, rows, totalModels: hm.models.length, allActions: hm.actions };
    }
    colLabel(key) {
        return key.replace("_", " ").toUpperCase();
    }
    heatClass(v) {
        if (!v) return "none";
        if (v <= 2) return "low";
        if (v <= 4) return "med";
        return "high";
    }
    moreColumns() {
        this.state.heatCols = this.state.heatCols === "all" ? "core" : "all";
    }
    lessModels() {
        this.state.heatModels = this.state.heatModels > 6 ? 6 : 12;
    }

    // ================= user inspector ================= //
    async onSelectUser(ev) {
        const id = parseInt(ev.target.value, 10);
        this.state.selectedUser = id || false;
        if (!id) {
            this.state.inspection = null;
            return;
        }
        this.state.inspection = await this.orm.call(
            "access.manager.profile", "get_user_inspection", [id]);
        this.state.inspectTab = "rules";
    }
    setTab(tab) {
        this.state.inspectTab = tab;
    }

    // ================= navigation / quick actions ================= //
    openProfile(id) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "access.manager.profile",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
    openList(name, domain, context) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "access.manager.profile",
            views: [[false, "list"], [false, "form"]],
            domain: domain || [],
            context: context || {},
            target: "current",
        });
    }
    createRule() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "access.manager.profile",
            views: [[false, "form"]],
            target: "current",
        });
    }
    async cleanupExpired() {
        await this.orm.call("access.manager.profile", "action_cleanup_expired", []);
        await this.load();
    }
    openImport() {
        this.actionService.doAction("access_manager_pro.action_access_import_wizard");
    }
    get quickActions() {
        const now = new Date();
        const in7 = new Date(now.getTime() + 7 * 864e5).toISOString().slice(0, 19).replace("T", " ");
        return [
            { label: "Create Rule", icon: "fa-plus", fn: () => this.createRule() },
            { label: "All Rules", icon: "fa-list", fn: () => this.openList("All Rules", []) },
            { label: "Cleanup Expired", icon: "fa-trash-o", fn: () => this.cleanupExpired() },
            { label: "Inactive Rules", icon: "fa-pause", fn: () => this.openList("Inactive Rules", [["active", "=", false]], { active_test: false }) },
            { label: "Expiring Soon", icon: "fa-hourglass-half", fn: () => this.openList("Expiring Soon", [["date_end", "!=", false], ["date_end", "<=", in7]]) },
            { label: "User Rules", icon: "fa-user", fn: () => this.openList("User Rules", [["restriction_type", "in", ["user", "mixed"]]]) },
            { label: "Group Rules", icon: "fa-users", fn: () => this.openList("Group Rules", [["restriction_type", "in", ["group", "mixed"]]]) },
            { label: "Time-Based", icon: "fa-clock-o", fn: () => this.openList("Time-Based", [["restriction_time_based", "=", true]]) },
            { label: "Import / Export", icon: "fa-exchange", fn: () => this.openImport() },
        ];
    }
}

registry.category("actions").add("access_manager_dashboard", AccessDashboard);
