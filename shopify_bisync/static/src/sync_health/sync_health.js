/** @odoo-module **/
// Part of Shopify Connector - Two-Way Sync. License OPL-1.
//
// OWL view widget rendered on the Shopify store form (<widget
// name="shopify_bisync_health"/>): live job-queue counters with one-click
// drill-down. Built on the Odoo Web Library (OWL) with the standard orm /
// action services - no external assets, no phone-home.

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

export class ShopifySyncHealth extends Component {
    static template = "shopify_bisync.SyncHealth";
    static props = { ...standardWidgetProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ counts: {}, loaded: false });
        this.meta = [
            { key: "pending", label: _t("Pending"), css: "text-bg-warning" },
            { key: "failed", label: _t("Failed"), css: "text-bg-danger" },
            { key: "done", label: _t("Done"), css: "text-bg-success" },
        ];
        onWillStart(() => this.load());
    }

    get resId() {
        return this.props.record.resId;
    }

    async load() {
        if (!this.resId) {
            this.state.loaded = true;
            return;
        }
        const counts = {};
        await Promise.all(
            this.meta.map(async ({ key }) => {
                counts[key] = await this.orm.searchCount("shopify.bisync.job", [
                    ["instance_id", "=", this.resId],
                    ["state", "=", key],
                ]);
            })
        );
        this.state.counts = counts;
        this.state.loaded = true;
    }

    async refresh() {
        this.state.loaded = false;
        await this.load();
    }

    openJobs(stateKey) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Sync Jobs"),
            res_model: "shopify.bisync.job",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [
                ["instance_id", "=", this.resId],
                ["state", "=", stateKey],
            ],
            target: "current",
        });
    }
}

registry
    .category("view_widgets")
    .add("shopify_bisync_health", { component: ShopifySyncHealth });
