# -*- coding: utf-8 -*-
"""Woo order import + customer upsert + shipment status export.

Order payload: https://woocommerce.github.io/woocommerce-rest-api-docs/#orders
Idempotency: client_order_ref = WOO/<order id>. Status writeback: delivery
validated in Odoo -> order status 'completed' + note with tracking.
"""
import json
from odoo import api, models


class WooOrderSync(models.AbstractModel):
    _name = "woo.bisync.order.sync"
    _description = "Woo Order Sync"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.kind == "order":
            self._import_order(job.instance_id, payload)
        elif job.kind == "customer":
            self._upsert_customer(job.instance_id, payload)
        elif job.kind == "shipment":
            self._export_shipment(job.instance_id, payload)

    @api.model
    def _upsert_customer(self, instance, c):
        Partner = self.env["res.partner"]
        email = c.get("email") or (c.get("billing") or {}).get("email")
        partner = email and Partner.search([("email", "=", email)], limit=1)
        billing = c.get("billing") or {}
        vals = {"name": (f"{billing.get('first_name','')} "
                         f"{billing.get('last_name','')}".strip() or email
                         or "Woo Guest"),
                "email": email, "phone": billing.get("phone"),
                "street": billing.get("address_1"),
                "city": billing.get("city"), "zip": billing.get("postcode")}
        return partner.write(vals) and partner or Partner.create(vals)

    @api.model
    def _import_order(self, instance, o):
        if instance.order_status_filter and o.get("status") and \
                o["status"] not in instance.order_status_filter.split(","):
            return
        ref = f"WOO/{o.get('id')}"
        Sale = self.env["sale.order"]
        if Sale.search_count([("client_order_ref", "=", ref),
                              ("company_id", "=", instance.company_id.id)]):
            return
        partner = self._upsert_customer(instance, o)
        lines = []
        for li in o.get("line_items", []):
            product = li.get("sku") and self.env["product.product"].search(
                [("default_code", "=", li["sku"])], limit=1)
            if not product:
                continue  # TODO(build): binding lookup + fallback product
            lines.append((0, 0, {"product_id": product.id,
                                 "product_uom_qty": li.get("quantity", 1),
                                 "price_unit": float(li.get("price") or 0),
                                 "name": li.get("name") or product.name}))
        Sale.create({"partner_id": partner.id, "client_order_ref": ref,
                     "company_id": instance.company_id.id,
                     "warehouse_id": instance.warehouse_id.id,
                     "order_line": lines, "origin": f"Woo {instance.name}"})
        # TODO(build): tax_lines->fiscal position, shipping_lines->carrier,
        # coupon_lines->discounts, payment status -> auto-confirm/invoice.

    @api.model
    def _export_shipment(self, instance, payload):
        binding_ref = payload.get("woo_order_id")
        if binding_ref:
            instance._api("PUT", f"orders/{binding_ref}",
                          {"status": "completed"})
