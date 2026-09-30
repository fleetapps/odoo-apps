# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""sale.order / sale.order.line extensions (in-place ``_inherit``).

The Shopify line-item id stored per order line is what makes order-edit
diffing (A2) and fulfillment-order allocation (A1) exact instead of
heuristic. This file also carries the Odoo -> Shopify triggers that live on
the order: quick-jump deep link, manual "mark paid", and the cancellation
push (loop-guarded via ``SKIP_ORDER_EXPORT``).
"""
from odoo import _, fields, models
from odoo.exceptions import UserError

#: context flag set by the inbound importer so an imported cancellation does
#: not bounce straight back to Shopify.
SKIP_ORDER_EXPORT = "shopify_bisync_skip_order_export"


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # Postgres does not collide on NULLs, so ordinary Odoo orders (both
    # columns NULL) are unaffected; only a genuine second import of the same
    # Shopify order is rejected. This is the database-level backstop under
    # the identity lookup in OrderSync._find_existing_order.
    _uniq_shopify_order = models.Constraint(
        "UNIQUE(shopify_bisync_instance_id, shopify_bisync_order_id)",
        "This Shopify order is already imported for this store.")

    shopify_bisync_instance_id = fields.Many2one(
        "shopify.bisync.instance", string="Shopify Store", readonly=True,
        index="btree_not_null", copy=False)
    shopify_bisync_order_id = fields.Char(
        string="Shopify Order ID", readonly=True, copy=False,
        index="btree_not_null")
    shopify_bisync_fulfillment_ids = fields.Char(
        string="Imported Shopify Fulfillments", readonly=True, copy=False,
        help="Comma-separated Shopify fulfillment ids already reflected in "
             "Odoo (idempotency for fulfillment-status import).")
    shopify_bisync_url = fields.Char(
        compute="_compute_shopify_bisync_url", string="Shopify Link")
    # Values mirror Shopify's own enums so the field means exactly what the
    # Shopify admin shows, with no lossy re-interpretation in between.
    shopify_bisync_risk_level = fields.Selection(
        [("HIGH", "High"), ("MEDIUM", "Medium"), ("LOW", "Low"),
         ("NONE", "None"), ("PENDING", "Pending")],
        string="Shopify Risk", readonly=True, copy=False,
        help="Worst risk level across Shopify's fraud assessments.")
    shopify_bisync_risk_recommendation = fields.Selection(
        [("ACCEPT", "Accept"), ("INVESTIGATE", "Investigate"),
         ("CANCEL", "Cancel"), ("NONE", "None")],
        string="Shopify Recommends", readonly=True, copy=False)

    def _compute_shopify_bisync_url(self):
        for order in self:
            instance = order.shopify_bisync_instance_id
            order.shopify_bisync_url = (
                instance.admin_url("orders", order.shopify_bisync_order_id)
                if instance and order.shopify_bisync_order_id else False)

    def action_shopify_open(self):
        """Quick-jump: open this order in the Shopify admin."""
        self.ensure_one()
        if not self.shopify_bisync_url:
            raise UserError(_("This order is not linked to a Shopify order."))
        return {"type": "ir.actions.act_url", "target": "new",
                "url": self.shopify_bisync_url}

    def action_shopify_mark_paid(self):
        """Manually queue a mark-as-paid push (bypasses the auto policy)."""
        Job = self.env["shopify.bisync.job"]
        for order in self.filtered("shopify_bisync_order_id"):
            Job.enqueue(order.shopify_bisync_instance_id, "out", "paid",
                        {"order_id": order.shopify_bisync_order_id,
                         "sale_order_id": order.id}, priority=14,
                        lock_key=f"order:{order.shopify_bisync_order_id}")
        return True

    def _action_cancel(self):
        """Push the cancellation to Shopify unless we are the ones importing
        it (skip flag) or the store opted out."""
        to_push = self.env["sale.order"]
        if not self.env.context.get(SKIP_ORDER_EXPORT):
            to_push = self.filtered(
                lambda o: o.shopify_bisync_order_id
                and o.shopify_bisync_instance_id.push_cancellations)
        res = super()._action_cancel()
        Job = self.env["shopify.bisync.job"]
        for order in to_push:
            Job.enqueue(order.shopify_bisync_instance_id, "out", "cancel",
                        {"order_id": order.shopify_bisync_order_id,
                         "sale_order_id": order.id,
                         "reason": "OTHER"}, priority=14,
                        lock_key=f"order:{order.shopify_bisync_order_id}")
        return res

    # --------- idempotency helpers for fulfillment-status import ------------
    def _shopify_fulfillment_done(self, fulfillment_id):
        done = set(filter(None, (self.shopify_bisync_fulfillment_ids or "")
                          .split(",")))
        return str(fulfillment_id) in done

    def _shopify_mark_fulfillment_done(self, fulfillment_id):
        done = set(filter(None, (self.shopify_bisync_fulfillment_ids or "")
                          .split(",")))
        done.add(str(fulfillment_id))
        self.shopify_bisync_fulfillment_ids = ",".join(sorted(done))


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    shopify_bisync_line_id = fields.Char(
        string="Shopify Line ID", readonly=True, copy=False)
