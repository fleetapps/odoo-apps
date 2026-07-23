# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Fulfillment export via the FulfillmentOrders API (spec A1).

The legacy ``POST /orders/{id}/fulfillments.json`` endpoint is gone: a
fulfillment now targets FulfillmentOrder line items. Flow per validated
outgoing picking:

1. GraphQL: fetch the order's open/in-progress fulfillment orders with their
   line items (each carries the original ``lineItem`` id we stored on the
   sale.order.line at import);
2. allocate this picking's done quantities across those FO line items
   (partial fulfillments per picking - two pickings become two fulfillments);
3. ``fulfillmentCreate`` with tracking number + carrier from the picking.
"""
import json
import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)

FULFILLMENT_ORDERS_QUERY = """
query fulfillmentOrders($id: ID!) {
  order(id: $id) {
    fulfillmentOrders(first: 20) {
      nodes {
        id status
        lineItems(first: 100) {
          nodes { id remainingQuantity lineItem { id sku } }
        }
      }
    }
  }
}"""

FULFILLMENT_CREATE = """
mutation fulfillmentCreate($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}"""

OPEN_FO_STATUSES = ("OPEN", "IN_PROGRESS", "SCHEDULED")


class FulfillmentSync(models.AbstractModel):
    _name = "shopify.bisync.fulfillment.sync"
    _description = "Fulfillment Export Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        self._export_fulfillment(job.instance_id, payload)

    @api.model
    def _picking_demand(self, picking):
        """{shopify line item id: qty done in THIS picking} - the basis of
        partial fulfillments."""
        demand = {}
        for move in picking.move_ids:
            line_id = move.sale_line_id.shopify_bisync_line_id
            if line_id and move.quantity:
                demand[line_id] = demand.get(line_id, 0) + int(move.quantity)
        return demand

    @api.model
    def _export_fulfillment(self, instance, payload):
        picking = self.env["stock.picking"].browse(
            payload.get("picking_id", 0)).exists()
        if not picking or picking.state != "done":
            return
        so = picking.sale_id
        if not so or not so.shopify_bisync_order_id:
            return
        demand = self._picking_demand(picking)
        if not demand:
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "fulfillment_gap",
                _("Picking %(picking)s has no moves linked to Shopify line "
                  "items - fulfillment not exported.", picking=picking.name),
                reference=so.client_order_ref, sale_order=so)
            return
        data = instance.graphql(FULFILLMENT_ORDERS_QUERY, {
            "id": instance.gid("Order", so.shopify_bisync_order_id)})
        nodes = (((data.get("order") or {}).get("fulfillmentOrders") or {})
                 .get("nodes") or [])
        remaining = dict(demand)
        by_fulfillment_order = []
        for fo in nodes:
            if fo.get("status") not in OPEN_FO_STATUSES:
                continue
            fo_lines = []
            for fo_line in (fo.get("lineItems") or {}).get("nodes") or []:
                original = instance.gid_to_id(
                    (fo_line.get("lineItem") or {}).get("id", ""))
                want = remaining.get(original, 0)
                available = int(fo_line.get("remainingQuantity") or 0)
                take = min(want, available)
                if take > 0:
                    fo_lines.append({"id": fo_line["id"], "quantity": take})
                    remaining[original] = want - take
            if fo_lines:
                by_fulfillment_order.append({
                    "fulfillmentOrderId": fo["id"],
                    "fulfillmentOrderLineItems": fo_lines})
        if not by_fulfillment_order:
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "fulfillment_gap",
                _("No open fulfillment order line matched picking "
                  "%(picking)s of %(order)s (already fulfilled on Shopify?).",
                  picking=picking.name, order=so.name),
                reference=so.client_order_ref, sale_order=so)
            return
        fulfillment = {
            "lineItemsByFulfillmentOrder": by_fulfillment_order,
            "notifyCustomer": instance.notify_customer_on_fulfillment,
        }
        if picking.carrier_tracking_ref:
            fulfillment["trackingInfo"] = {
                "number": picking.carrier_tracking_ref,
                "company": picking.carrier_id.name or _("Other"),
            }
        data = instance.graphql(FULFILLMENT_CREATE,
                                {"fulfillment": fulfillment})
        result = data.get("fulfillmentCreate") or {}
        instance.check_user_errors(result, "fulfillmentCreate")
        fulfillment_id = (result.get("fulfillment") or {}).get("id", "?")
        picking.message_post(body=_(
            "Pushed to Shopify as fulfillment %(fid)s (tracking: %(track)s).",
            fid=fulfillment_id,
            track=picking.carrier_tracking_ref or "-"))
        unshipped = {k: v for k, v in remaining.items() if v > 0}
        if unshipped:
            _logger.info("shopify_bisync: picking %s quantities not accepted "
                         "by any fulfillment order: %s",
                         picking.name, unshipped)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        res = super().button_validate()
        Job = self.env["shopify.bisync.job"]
        for picking in self.filtered(
                lambda p: p.state == "done"
                and p.picking_type_code == "outgoing"
                and p.sale_id.shopify_bisync_instance_id
                and p.sale_id.shopify_bisync_order_id):
            instance = picking.sale_id.shopify_bisync_instance_id
            Job.enqueue(instance, "out", "fulfillment",
                        {"picking_id": picking.id}, priority=12,
                        lock_key=f"order:{picking.sale_id.shopify_bisync_order_id}")
        return res
