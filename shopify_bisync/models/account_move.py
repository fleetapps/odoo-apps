# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""account.move triggers for Odoo -> Shopify money status.

- When a Shopify order's customer invoice becomes fully paid in Odoo, queue
  a mark-as-paid push (if the store opted in).
- When a credit note is posted in Odoo for a Shopify order (and it did not
  itself come from a Shopify refund), queue a refund push (if enabled).

``shopify_bisync_refund_id`` both marks refunds we imported (so they never
bounce back) and records the Shopify refund id created by an export.
"""
from odoo import fields, models

SKIP_PAID_PUSH = "shopify_bisync_skip_paid_push"


class AccountMove(models.Model):
    _inherit = "account.move"

    shopify_bisync_refund_id = fields.Char(
        string="Shopify Refund ID", readonly=True, copy=False,
        index="btree_not_null")

    def _shopify_sale_orders(self):
        """Shopify-origin sale orders linked to these moves via their lines."""
        return self.line_ids.sale_line_ids.order_id.filtered(
            lambda so: so.shopify_bisync_instance_id
            and so.shopify_bisync_order_id)

    def write(self, vals):
        res = super().write(vals)
        # payment_state is a stored computed field; its own recompute writes
        # here when reconciliation completes.
        if vals.get("payment_state") in ("paid", "in_payment"):
            self._shopify_enqueue_paid()
        return res

    def _shopify_enqueue_paid(self):
        if self.env.context.get(SKIP_PAID_PUSH):
            return
        Job = self.env["shopify.bisync.job"]
        for move in self.filtered(lambda m: m.move_type == "out_invoice"):
            for so in move._shopify_sale_orders():
                instance = so.shopify_bisync_instance_id
                if instance.push_paid_status:
                    Job.enqueue(instance, "out", "paid",
                                {"order_id": so.shopify_bisync_order_id,
                                 "sale_order_id": so.id}, priority=14,
                                lock_key=f"order:{so.shopify_bisync_order_id}")

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted._shopify_enqueue_refund_export()
        return posted

    def _shopify_enqueue_refund_export(self):
        Job = self.env["shopify.bisync.job"]
        for move in self.filtered(
                lambda m: m.move_type == "out_refund"
                and not m.shopify_bisync_refund_id):
            for so in move._shopify_sale_orders():
                instance = so.shopify_bisync_instance_id
                if instance.refund_export_policy != "off":
                    Job.enqueue(instance, "out", "refund_out",
                                {"move_id": move.id,
                                 "order_id": so.shopify_bisync_order_id},
                                priority=14,
                                lock_key=f"order:{so.shopify_bisync_order_id}")
