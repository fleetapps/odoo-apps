/** @odoo-module **/
/**
 * MCP Governance dashboard - the app's landing page, as an OWL client action.
 *
 * The module had no interface of its own: four bare list views and a master
 * switch buried in Settings, with nothing anywhere saying which to open first.
 * This screen answers the three questions an administrator actually has -
 * is the endpoint live, who is calling it, and what is waiting on me.
 *
 * Charts are inline SVG built from geometry computed here, so there is no
 * charting dependency to keep in step with the web bundle, and the whole
 * screen renders from a single `get_dashboard_data` round trip.
 */
import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const OK_COLOR = "#2dd4bf";
const ERR_COLOR = "#e0533d";
const BAR_COLORS = ["#6d28d9", "#3b82f6", "#0f8f8f", "#d6409f", "#e0a417", "#64748b"];

export class MCPDashboard extends Component {
    static template = "mcp_governance_suite.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        // Plain properties, not getters: the template interpolates them into
        // every bar's fill, and a getter would re-run on each of them.
        this.okColor = OK_COLOR;
        this.errColor = ERR_COLOR;
        this.state = useState({ loading: true, data: null, busy: false });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "mcp.dashboard", "get_dashboard_data", []);
        } finally {
            this.state.loading = false;
        }
    }

    refresh() {
        return this.load();
    }

    // ================== derived state ================== //
    get status() {
        return this.state.data.status;
    }
    get kpis() {
        return this.state.data.kpis;
    }
    get setupDone() {
        return this.state.data.setup.every((s) => s.done);
    }

    get kpiCards() {
        const k = this.kpis;
        const trend =
            k.calls_trend === null
                ? "no prior week"
                : `${k.calls_trend > 0 ? "+" : ""}${k.calls_trend}% vs prior week`;
        return [
            { label: "Calls (24h)", value: k.calls_24h, sub: `${k.calls_7d} in 7 days`,
              accent: "violet", icon: "fa-exchange" },
            { label: "Calls (7d)", value: k.calls_7d, sub: trend,
              accent: "blue", icon: "fa-line-chart" },
            { label: "Errors (7d)", value: k.errors_7d, sub: `${k.error_pct}% of calls`,
              accent: k.errors_7d ? "red" : "slate", icon: "fa-exclamation-triangle" },
            { label: "Pending Approvals", value: k.pending_approvals,
              sub: k.pending_approvals ? "waiting on a human" : "nothing queued",
              accent: k.pending_approvals ? "amber" : "slate", icon: "fa-gavel" },
            { label: "Active Keys", value: k.keys_active, sub: `${k.keys_total} total`,
              accent: "teal", icon: "fa-key" },
            { label: "Keys Expiring ≤30d", value: k.keys_expiring,
              sub: k.keys_expiring ? "reissue before they lapse" : "none",
              accent: k.keys_expiring ? "amber" : "slate", icon: "fa-hourglass-half" },
            { label: "Scopes", value: k.scopes,
              sub: `${k.scopes_writable} allow writes`, accent: "magenta", icon: "fa-shield" },
            { label: "Est. Tokens (7d)", value: this.compact(k.tokens_7d),
              sub: "rough size-based estimate", accent: "dark", icon: "fa-database" },
        ];
    }

    compact(n) {
        if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
        if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
        if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
        return `${n || 0}`;
    }

    // ================== activity chart ================== //
    /**
     * Stacked ok/error columns with a y-axis. Days with no traffic keep their
     * slot (the server sends a dense calendar) so the shape of the week is
     * honest rather than compressed.
     */
    get chart() {
        const series = this.state.data.series;
        const w = 720, h = 190, padL = 34, padR = 8, padT = 12, padB = 26;
        const max = Math.max(...series.map((d) => d.total), 1);
        const innerW = w - padL - padR;
        const step = innerW / series.length;
        const bw = Math.min(30, step * 0.62);
        const y = (v) => (h - padB) - (v / max) * (h - padB - padT);
        const cols = series.map((d, i) => {
            const x = padL + step * i + (step - bw) / 2;
            const okH = (h - padB) - y(d.ok);
            const errH = (h - padB) - y(d.error);
            return {
                label: d.label,
                short: d.short,
                total: d.total,
                ok: d.ok,
                error: d.error,
                x,
                w: bw,
                cx: padL + step * i + step / 2,
                okY: (h - padB) - okH - errH,
                okH,
                errY: (h - padB) - errH,
                errH,
            };
        });
        const ticks = [0, Math.round(max / 2), max].map((v) => ({ v, y: y(v) }));
        return { w, h, padL, right: w - padR, baseline: h - padB, cols, ticks,
                 empty: series.every((d) => !d.total) };
    }

    bars(rows) {
        const max = Math.max(...rows.map((r) => r.value), 1);
        return rows.map((r, i) => ({
            ...r,
            pct: Math.max(4, (r.value / max) * 100),
            color: BAR_COLORS[i % BAR_COLORS.length],
        }));
    }
    get toolBars() {
        return this.bars(this.state.data.top_tools);
    }
    get keyBars() {
        return this.bars(this.state.data.top_keys);
    }

    // ================== approvals ================== //
    async approve(id) {
        await this._decide("approve", id);
    }
    async reject(id) {
        await this._decide("reject", id);
    }
    async _decide(verb, id) {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        try {
            const res = await this.orm.call("mcp.dashboard", verb, [id]);
            this.notification.add(
                verb === "approve"
                    ? `Request ${res.state === "executed" ? "executed" : res.state}.`
                    : "Request rejected.",
                { type: res.state === "failed" ? "danger" : "success" });
            await this.load();
        } finally {
            this.state.busy = false;
        }
    }

    // ================== navigation ================== //
    openAction(xmlid) {
        return this.action.doAction(`mcp_governance_suite.${xmlid}`);
    }
    open(target) {
        const map = {
            keys: "mcp_key_action",
            scopes: "mcp_scope_action",
            audit: "mcp_audit_action",
            approvals: "mcp_approval_action",
            settings: "mcp_config_settings_action",
        };
        return this.openAction(map[target] || "mcp_key_action");
    }
    openRecord(model, resId) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: resId,
            views: [[false, "form"]],
            target: "current",
        });
    }
    newRecord(model) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async copyEndpoint() {
        try {
            await navigator.clipboard.writeText(this.status.endpoint);
            this.notification.add("Endpoint copied.", { type: "success" });
        } catch {
            this.notification.add(this.status.endpoint, { type: "info", sticky: true });
        }
    }

    get quickActions() {
        return [
            { label: "New Scope", icon: "fa-shield", fn: () => this.newRecord("mcp.scope") },
            { label: "New API Key", icon: "fa-key", fn: () => this.newRecord("mcp.api.key") },
            { label: "Approvals", icon: "fa-gavel", fn: () => this.open("approvals") },
            { label: "Audit Log", icon: "fa-list", fn: () => this.open("audit") },
            { label: "Settings", icon: "fa-cog", fn: () => this.open("settings") },
        ];
    }
}

registry.category("actions").add("mcp_governance_dashboard", MCPDashboard);
