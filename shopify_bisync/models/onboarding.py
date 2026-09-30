# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Onboarding panel wiring.

Uses Odoo's own onboarding machinery (``onboarding.onboarding`` /
``onboarding.onboarding.step``, addons/onboarding) so progress is stored and
scoped per company exactly like Invoicing's setup panel, rather than being a
bespoke checklist.

The one thing we do differently from most core panels: step completion is
*derived from real state*, not from the user having clicked the button. A step
that says "done" because someone opened a dialog and closed it is worse than no
panel at all, so :meth:`ShopifyInstance.onboarding_panel_data` reads the actual
records - is there a store, did Test Connection last succeed, are webhooks
registered, is a location mapped to a warehouse, has a backfill ever run.
"""
from odoo import _, api, fields, models

#: step xmlid suffix -> (sequence, what makes it done)
STEP_ORDER = (
    "step_connect_store",
    "step_test_connection",
    "step_webhooks",
    "step_locations",
    "step_backfill",
)


class OnboardingStep(models.Model):
    _inherit = "onboarding.onboarding.step"

    # -- panel_step_open_action_name targets -------------------------------
    # Each returns the action the panel should open for that step. They are
    # called by name from the dashboard widget, so the names must match the
    # panel_step_open_action_name values in data/onboarding_data.xml.

    @api.model
    def action_open_step_connect_store(self):
        return self._shopify_store_action(_("Connect your Shopify store"))

    @api.model
    def action_open_step_test_connection(self):
        return self._shopify_store_action(_("Test the connection"))

    @api.model
    def action_open_step_webhooks(self):
        return self._shopify_store_action(_("Register webhooks"))

    @api.model
    def action_open_step_locations(self):
        return self._shopify_store_action(_("Map Shopify locations"))

    @api.model
    def action_open_step_backfill(self):
        return self.env["ir.actions.actions"]._for_xml_id(
            "shopify_bisync.backfill_action")

    @api.model
    def _shopify_store_action(self, name):
        """Open the single store directly, or the list when there are 0 or 2+.

        Dropping a first-time user on a list with one row to click is the kind
        of small friction this panel exists to remove.
        """
        action = self.env["ir.actions.actions"]._for_xml_id(
            "shopify_bisync.instance_action")
        action["name"] = name
        instances = self.env["shopify.bisync.instance"].search([], limit=2)
        if len(instances) == 1:
            action.update({
                "view_mode": "form",
                "views": [(False, "form")],
                "res_id": instances.id,
            })
        return action


class ShopifyInstanceOnboarding(models.Model):
    _inherit = "shopify.bisync.instance"

    @api.model
    def onboarding_panel_data(self):
        """Steps + real completion state for the dashboard panel.

        Returns a plain list so the OWL widget stays dumb; each entry carries
        the method name to call when its button is clicked.
        """
        done = self._onboarding_done_map()
        out = []
        for suffix in STEP_ORDER:
            step = self.env.ref(f"shopify_bisync.onboarding_{suffix}",
                                raise_if_not_found=False)
            if not step:
                continue
            out.append({
                "id": step.id,
                "title": step.title,
                "description": step.description,
                "button_text": step.button_text,
                "done_text": step.done_text,
                "action": step.panel_step_open_action_name,
                "done": done.get(suffix, False),
            })
        return {
            "steps": out,
            "all_done": all(s["done"] for s in out) if out else False,
            "text_completed": _("Your Shopify store is syncing."),
        }

    @api.model
    def _onboarding_done_map(self):
        """What is *actually* configured, independent of button clicks.

        sudo: the panel is shown to Shopify Sync *users*, but access_token is
        restricted to the admin group, so an unprivileged read would raise.
        Only booleans escape this method - no credential is ever returned.
        """
        instance = self.sudo().search([], limit=1, order="id")
        if not instance:
            return {}
        has_mapped_location = bool(instance.location_map_ids.filtered(
            lambda location: location.warehouse_id))
        # Match the dedicated 'backfill' job kind, not product/order/customer:
        # those are also enqueued by ordinary webhook traffic, which would mark
        # the step done for a store that never ran an initial import.
        backfill_ran = bool(self.env["shopify.bisync.job"].sudo().search_count([
            ("instance_id", "=", instance.id),
            ("kind", "=", "backfill"),
        ]))
        return {
            "step_connect_store": bool(instance.access_token),
            "step_test_connection": bool(instance.connection_ok_on),
            "step_webhooks": bool(instance.webhooks_registered_on),
            "step_locations": has_mapped_location,
            "step_backfill": backfill_ran,
        }
