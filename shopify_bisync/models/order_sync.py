# -*- coding: utf-8 -*-
"""Order import (webhook-driven), customer upsert, fulfillment export, refunds.

Order flow: orders/create webhook -> job -> sale.order (client_order_ref =
Shopify order number for idempotency) -> optional auto-confirm.
Fulfillment: stock.picking done in Odoo -> export tracking to Shopify.
"""
import json
from odoo import api, models


class OrderSync(models.AbstractModel):
    _name = "shopify.bisync.order.sync"
    _description = "Order/Customer/Fulfillment Sync Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.kind == "order":
            self._import_order(job.instance_id, payload)
        elif job.kind == "customer":
            self._upsert_customer(job.instance_id, payload)
        elif job.kind == "fulfillment":
            self._export_fulfillment(job.instance_id, payload)
        elif job.kind == "refund":
            self._import_refund(job.instance_id, payload)

    @api.model
    def _upsert_customer(self, instance, c):
        Partner = self.env["res.partner"]
        email = c.get("email")
        partner = email and Partner.search([("email", "=", email)], limit=1)
        vals = {"name": (f"{c.get('first_name','')} {c.get('last_name','')}".strip()
                         or email or "Shopify Guest"),
                "email": email, "phone": c.get("phone")}
        addr = (c.get("default_address") or {})
        vals.update({"street": addr.get("address1"), "city": addr.get("city"),
                     "zip": addr.get("zip")})
        # TODO(build): country/state resolution via res.country(.state) codes
        return partner.write(vals) and partner or Partner.create(vals)

    @api.model
    def _import_order(self, instance, o):
        ref = f"SHOPIFY/{o.get('order_number') or o.get('id')}"
        Sale = self.env["sale.order"]
        if Sale.search_count([("client_order_ref", "=", ref),
                              ("company_id", "=", instance.company_id.id)]):
            return  # idempotent: webhooks re-deliver
        partner = self._upsert_customer(instance, o.get("customer") or {})
        lines = []
        Binding = self.env["shopify.bisync.binding"]
        for li in o.get("line_items", []):
            product = None
            if li.get("sku"):
                product = self.env["product.product"].search(
                    [("default_code", "=", li["sku"])], limit=1)
            if not product and li.get("product_id"):
                b = Binding.search([("instance_id", "=", instance.id),
                                    ("res_model", "=", "product.template"),
                                    ("external_id", "=", str(li["product_id"]))],
                                   limit=1)
                tmpl = b and b.resolve()
                product = tmpl and tmpl.product_variant_id
            if not product:
                # TODO(build): configurable fallback product + mismatch report
                continue
            lines.append((0, 0, {
                "product_id": product.id,
                "product_uom_qty": li.get("quantity", 1),
                "price_unit": float(li.get("price") or 0),
                "name": li.get("title") or product.name,
            }))
        Sale.create({
            "partner_id": partner.id,
            "client_order_ref": ref,
            "company_id": instance.company_id.id,
            "warehouse_id": instance.warehouse_id.id,
            "order_line": lines,
            "origin": f"Shopify {instance.name}",
        })
        # TODO(build): taxes strategy (Shopify tax_lines vs Odoo fiscal positions),
        # shipping as delivery.carrier line, discounts, financial_status ->
        # auto-confirm + invoice policy per instance setting.

    @api.model
    def _export_fulfillment(self, instance, payload):
        """Called when a delivery is validated: push tracking to Shopify.
        Fulfillment API (FulfillmentOrders) - VERIFY-ON-BUILD, this API changed
        significantly vs legacy /fulfillments."""
        # scaffold stub

    @api.model
    def _import_refund(self, instance, payload):
        """Create credit note draft mirroring the Shopify refund."""
        # scaffold stub


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        res = super().button_validate()
        Job = self.env["shopify.bisync.job"]
        for picking in self.filtered(lambda p: p.picking_type_code == "outgoing"
                                     and p.sale_id
                                     and (p.sale_id.client_order_ref or "").startswith("SHOPIFY/")):
            for instance in self.env["shopify.bisync.instance"].search([]):
                Job.enqueue(instance, "out", "fulfillment",
                            {"picking_id": picking.id,
                             "tracking": picking.carrier_tracking_ref or ""})
        return res
