/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";

/**
 * Tell an open Odoo tab that a dashboard has arrived.
 *
 * The moment this exists to fix: you ask Claude for a dashboard, switch back to
 * Odoo, and have no idea whether it worked — so you refresh, and refresh again.
 * The server clears its menu cache on its own, but an already-loaded browser has
 * no way to know anything happened. This closes that gap, and turns the worst
 * moment in the flow into the best one.
 *
 * Deliberately a toast with a button rather than an automatic navigation: you
 * may well be in the middle of something else, and a page that jumps somewhere
 * because a background job finished is worse than one that waits to be asked.
 */
export const aiDashboardReadyService = {
    dependencies: ["bus_service", "notification", "action"],

    start(env, { bus_service, notification, action }) {
        bus_service.subscribe("ai_dashboards.ready", (payload) => {
            if (!payload || !payload.id) {
                return;
            }
            const isPreview = payload.state === "draft";
            notification.add(payload.headline || _t("Your dashboard is ready."), {
                type: "success",
                sticky: isPreview, // a preview needs a decision, so let it wait
                buttons: [
                    {
                        name: isPreview ? _t("Review it") : _t("Open it"),
                        primary: true,
                        onClick: () =>
                            action.doAction({
                                type: "ir.actions.act_window",
                                name: payload.name,
                                res_model: "ai.dashboard",
                                res_id: payload.id,
                                view_mode: "form",
                                views: [[false, "form"]],
                                target: "current",
                            }),
                    },
                ],
            });
        });
        bus_service.start();
    },
};

registry.category("services").add("ai_dashboards.ready", aiDashboardReadyService);
