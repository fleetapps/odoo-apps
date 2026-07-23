# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Odoo -> Shopify order status: mark-as-paid and cancellation push.

Both are opt-in per instance (they change money/inventory on Shopify) and
loop-guarded: the inbound importers set skip-context flags so an imported
payment or cancellation never bounces straight back to Shopify.
"""
import json
import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)

ORDER_MARK_PAID = """
mutation orderMarkAsPaid($input: OrderMarkAsPaidInput!) {
  orderMarkAsPaid(input: $input) {
    order { id displayFinancialStatus }
    userErrors { field message }
  }
}"""

ORDER_CANCEL = """
mutation orderCancel($orderId: ID!, $reason: OrderCancelReason!,
                     $refund: Boolean!, $restock: Boolean!,
                     $notifyCustomer: Boolean, $staffNote: String) {
  orderCancel(orderId: $orderId, reason: $reason, refund: $refund,
              restock: $restock, notifyCustomer: $notifyCustomer,
              staffNote: $staffNote) {
    job { id }
    orderCancelUserErrors { field message }
  }
}"""


class OrderExportSync(models.AbstractModel):
    _name = "shopify.bisync.order.export.sync"
    _description = "Order Status Export Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.kind == "paid":
            self._mark_paid(job.instance_id, payload)
        elif job.kind == "cancel":
            self._cancel(job.instance_id, payload)

    @api.model
    def _mark_paid(self, instance, payload):
        if not instance.push_paid_status or not payload.get("order_id"):
            return
        data = instance.graphql(ORDER_MARK_PAID, {"input": {
            "id": instance.gid("Order", payload["order_id"])}})
        result = data.get("orderMarkAsPaid") or {}
        errors = result.get("userErrors") or []
        if errors:
            # The common case is "order already paid / nothing to capture":
            # benign, and retrying won't help - log instead of quarantining.
            _logger.info("shopify_bisync: orderMarkAsPaid on %s: %s",
                         payload["order_id"],
                         "; ".join(e.get("message", "") for e in errors))
            return
        so = self.env["sale.order"].browse(payload.get("sale_order_id", 0))
        if so.exists():
            so.message_post(body=_("Marked as paid on Shopify."))

    @api.model
    def _cancel(self, instance, payload):
        if not instance.push_cancellations or not payload.get("order_id"):
            return
        data = instance.graphql(ORDER_CANCEL, {
            "orderId": instance.gid("Order", payload["order_id"]),
            "reason": payload.get("reason", "OTHER"),
            "refund": False,  # money movement stays a merchant decision
            "restock": instance.cancel_restock,
            "notifyCustomer": False,
            "staffNote": payload.get("note") or _("Cancelled from Odoo."),
        })
        instance.check_user_errors(data.get("orderCancel") or {}, "orderCancel")
        so = self.env["sale.order"].browse(payload.get("sale_order_id", 0))
        if so.exists():
            so.message_post(body=_("Cancellation pushed to Shopify."))
