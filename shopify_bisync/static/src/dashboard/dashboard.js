/** @odoo-module **/
// Part of Shopify Connector - Two-Way Sync. License OPL-1.
//
// Sales dashboard client action: KPI cards + top products / categories /
// countries + per-store comparison, from shopify.bisync.sale.report. Pure
// OWL + orm service, no external chart libraries (store review: no CDN).

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { Layout } from "@web/search/layout";

export class ShopifyDashboard extends Component {
    static template = "shopify_bisync.Dashboard";
    static components = { Layout };
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, data: null, onboarding: null });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        const [data, onboarding] = await Promise.all([
            this.orm.call("shopify.bisync.sale.report", "dashboard_data", [], {}),
            this.orm.call("shopify.bisync.instance", "onboarding_panel_data", [], {}),
        ]);
        this.state.data = data;
        this.state.onboarding = onboarding;
        this.state.loading = false;
    }

    // The panel is only useful while setup is incomplete; once every step is
    // done it would just be chrome on a working dashboard, so it hides itself.
    get showOnboarding() {
        return this.state.onboarding && !this.state.onboarding.all_done;
    }

    get onboardingSteps() {
        return this.state.onboarding ? this.state.onboarding.steps : [];
    }

    get onboardingDoneCount() {
        return this.onboardingSteps.filter((step) => step.done).length;
    }

    async openStep(step) {
        const action = await this.orm.call(
            "onboarding.onboarding.step",
            step.action,
            [],
            {}
        );
        this.action.doAction(action, {
            // Re-read completion when the user comes back, so a step ticks over
            // as soon as it is genuinely done rather than on next page load.
            onClose: () => this.load(),
        });
    }

    get panels() {
        const d = this.state.data;
        if (!d) {
            return [];
        }
        return [
            { title: _t("Top Products"), rows: d.top_products },
            { title: _t("Top Categories"), rows: d.top_categories },
            { title: _t("Top Countries"), rows: d.top_countries },
            { title: _t("By Store"), rows: d.by_instance },
        ];
    }

    format(amount) {
        const c = this.state.data?.currency || { symbol: "", position: "before" };
        const n = (amount || 0).toLocaleString(undefined, {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
        return c.position === "after" ? `${n} ${c.symbol}` : `${c.symbol}${n}`;
    }

    barWidth(value, rows) {
        const max = Math.max(1, ...rows.map((r) => r.total));
        return `${Math.round((value / max) * 100)}%`;
    }

    openOrders() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Shopify Orders"),
            res_model: "sale.order",
            domain: [["shopify_bisync_instance_id", "!=", false]],
            views: [
                [false, "list"],
                [false, "form"],
            ],
            target: "current",
        });
    }
}

registry.category("actions").add("shopify_bisync_dashboard", ShopifyDashboard);
