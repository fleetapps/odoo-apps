/** @odoo-module **/
// Part of Shopify Connector - Two-Way Sync. License OPL-1.
//
// Sync health client action. Separate from the sales dashboard on purpose:
// this one answers "is the connector working", not "how is the shop trading".
// Pure OWL + orm service, no chart library (store review: no CDN assets).

import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Layout } from "@web/search/layout";

// Slow enough to be invisible on the server, fast enough that a backfill
// visibly moves while someone is watching it.
const REFRESH_MS = 15000;

export class ShopifySyncDashboard extends Component {
    static template = "shopify_bisync.SyncDashboard";
    static components = { Layout };
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            data: null,
            filter: "all",
            instanceId: null,
        });
        onWillStart(() => this.load());
        this.timer = setInterval(() => this.load(true), REFRESH_MS);
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load(quiet = false) {
        if (!quiet) {
            this.state.loading = true;
        }
        this.state.data = await this.orm.call(
            "shopify.bisync.job", "sync_dashboard_data",
            [], { instance_id: this.state.instanceId }
        );
        this.state.loading = false;
    }

    get rows() {
        const rows = this.state.data ? this.state.data.rows : [];
        if (this.state.filter === "synced") {
            return rows.filter((r) => r.state === "done");
        }
        if (this.state.filter === "not_synced") {
            return rows.filter((r) => r.state !== "done");
        }
        return rows;
    }

    // No attempts yet means no data, not a 0% success rate — on a fresh store
    // "0%" reads as "everything is broken".
    get successRate() {
        const rate = this.state.data && this.state.data.success_rate;
        return rate === null || rate === undefined ? "—" : `${rate}%`;
    }

    setFilter(filter) {
        this.state.filter = filter;
    }

    async setStore(ev) {
        const value = ev.target.value;
        this.state.instanceId = value ? parseInt(value, 10) : null;
        await this.load();
    }

    async retryAllFailed() {
        const count = await this.orm.call(
            "shopify.bisync.job", "action_retry_all_failed",
            [], { instance_id: this.state.instanceId }
        );
        this.notification.add(
            count
                ? _t("%s job(s) put back in the queue.", count)
                : _t("Nothing to retry."),
            { type: count ? "success" : "info" }
        );
        await this.load();
    }

    openJob(row) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "shopify.bisync.job",
            res_id: row.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openFailedJobs() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Jobs needing attention"),
            res_model: "shopify.bisync.job",
            domain: [["state", "=", "failed"]],
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    stateLabel(row) {
        return {
            done: _t("Synced"),
            failed: _t("Failed"),
            pending: _t("Waiting"),
            skipped: _t("Skipped"),
        }[row.state] || row.state;
    }

    stateClass(row) {
        return {
            done: "text-bg-success",
            failed: "text-bg-danger",
            pending: "text-bg-warning",
            skipped: "text-bg-secondary",
        }[row.state] || "text-bg-secondary";
    }
}

registry.category("actions").add(
    "shopify_bisync_sync_dashboard", ShopifySyncDashboard);
