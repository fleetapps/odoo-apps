# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Fulfillment sync via the FulfillmentOrders API (spec A1 + bi-directional).

EXPORT (Odoo -> Shopify) - the legacy ``POST /orders/{id}/fulfillments.json``
endpoint is gone: a fulfillment now targets FulfillmentOrder line items. Flow
per validated outgoing picking:

1. GraphQL: fetch the order's open/in-progress fulfillment orders with their
   line items (each carries the original ``lineItem`` id we stored on the
   sale.order.line at import);
2. allocate this picking's done quantities across those FO line items
   (partial fulfillments per picking - two pickings become two fulfillments);
3. ``fulfillmentCreate`` with tracking number + URL + carrier.

IMPORT (Shopify -> Odoo) - ``fulfillments/create|update`` webhooks (and the
``fulfillments`` array on an imported order) confirm the order and validate
the matching delivery so Odoo shows it shipped. Loop-guarded via
``SKIP_FULFILLMENT_EXPORT`` so reflecting a Shopify fulfillment never pushes
a new one back.
"""
import json
import logging

from odoo import _, api, models

from .sale_order import SKIP_ORDER_EXPORT

_logger = logging.getLogger(__name__)

#: context flag: this picking validation mirrors a Shopify fulfillment, so
#: the button_validate trigger must not re-export it.
SKIP_FULFILLMENT_EXPORT = "shopify_bisync_skip_fulfillment_export"

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
        if job.direction == "in":
            self._import_fulfillment(job.instance_id, payload)
        else:
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
            tracking = {"number": picking.carrier_tracking_ref,
                        "company": picking.carrier_id.name or _("Other")}
            url = self._tracking_url(picking)
            if url:
                tracking["url"] = url
            fulfillment["trackingInfo"] = tracking
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

    @api.model
    def _tracking_url(self, picking):
        """Carrier tracking URL if the delivery carrier exposes one."""
        carrier = picking.carrier_id
        if not carrier:
            return None
        try:
            tracking = carrier.get_tracking_link(picking)
        except Exception:  # noqa: BLE001 - carriers vary; URL is optional
            return None
        if isinstance(tracking, (list, tuple)):
            tracking = tracking[0] if tracking else None
        return tracking or None

    # ------------------------------------------------------------- import ---
    @api.model
    def _import_fulfillment(self, instance, fulfillment):
        """A Shopify Fulfillment (webhook payload) -> validate the matching
        Odoo delivery so the order shows shipped."""
        if not instance.import_fulfillment_status:
            return
        binding = self.env["shopify.bisync.binding"].get(
            self.env, instance, "sale.order",
            external_id=fulfillment.get("order_id"))
        so = binding and binding.resolve()
        if not so:
            self.env["shopify.bisync.mismatch"].log(
                self.env, instance, "fulfillment_gap",
                _("Fulfillment %(fid)s references Shopify order %(oid)s not "
                  "imported in Odoo.", fid=fulfillment.get("id"),
                  oid=fulfillment.get("order_id")),
                reference=str(fulfillment.get("order_id")))
            return
        self._apply_fulfillment(instance, so, fulfillment)

    @api.model
    def _apply_fulfillment(self, instance, so, fulfillment):
        """Idempotently reflect one Shopify fulfillment on the Odoo order.
        Callable from the webhook path and from order import (the order's
        embedded ``fulfillments`` array)."""
        if not instance.import_fulfillment_status:
            return
        fid = str(fulfillment.get("id") or "")
        if fid and so._shopify_fulfillment_done(fid):
            return  # webhook redelivery / already applied
        if fulfillment.get("status") in ("cancelled", "error", "failure"):
            return
        if so.state in ("draft", "sent"):
            try:
                so.with_context(**{SKIP_ORDER_EXPORT: True}).action_confirm()
            except Exception:  # noqa: BLE001 - keep the order, tell the human
                _logger.exception("shopify_bisync: confirm-on-fulfillment "
                                  "failed for %s", so.name)
                return
        if so.state != "sale":
            return
        demand = {}
        for li in fulfillment.get("line_items") or []:
            lid = str(li.get("id") or li.get("line_item_id") or "")
            if lid:
                demand[lid] = demand.get(lid, 0) + int(li.get("quantity") or 0)
        self._validate_pickings(so, demand)
        if fid:
            so._shopify_mark_fulfillment_done(fid)

    @api.model
    def _validate_pickings(self, so, demand):
        """Set done quantities on the outgoing pickings and validate them,
        splitting a backorder for anything not covered. ``demand`` empty =>
        fulfill everything still open."""
        Backorder = self.env["stock.backorder.confirmation"]
        remaining = dict(demand)
        pickings = so.picking_ids.filtered(
            lambda p: p.picking_type_code == "outgoing"
            and p.state not in ("done", "cancel"))
        for picking in pickings:
            touched = False
            for move in picking.move_ids:
                lid = move.sale_line_id.shopify_bisync_line_id
                if demand:
                    want = remaining.get(lid, 0) if lid else 0
                    take = min(want, move.product_uom_qty)
                    move.quantity = take
                    if lid:
                        remaining[lid] = want - take
                else:
                    move.quantity = move.product_uom_qty
                    take = move.product_uom_qty
                if "picked" in move._fields:
                    move.picked = bool(take)
                touched = touched or bool(take)
            if not touched:
                continue
            picking = picking.with_context(**{SKIP_FULFILLMENT_EXPORT: True})
            result = picking.button_validate()
            if isinstance(result, dict) and result.get("res_model") == \
                    "stock.backorder.confirmation":
                Backorder.with_context(**result.get("context", {})).create(
                    {}).process()  # create the backorder for the rest


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        res = super().button_validate()
        if self.env.context.get(SKIP_FULFILLMENT_EXPORT):
            return res  # this validation mirrors a Shopify fulfillment
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
