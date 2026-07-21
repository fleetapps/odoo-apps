# -*- coding: utf-8 -*-
"""Product & stock two-way sync for WooCommerce.

Woo product payload refs:
https://woocommerce.github.io/woocommerce-rest-api-docs/#products
Key Woo fields: regular_price (string), sku, stock_quantity, manage_stock.
"""
import hashlib
import json
from odoo import api, fields, models


class WooProductSync(models.AbstractModel):
    _name = "woo.bisync.product.sync"
    _description = "Woo Product/Stock Sync"

    WATCHED_FIELDS = ("name", "list_price", "default_code", "barcode",
                      "description_sale", "active")

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.kind == "stock":
            return self._export_stock(job.instance_id, payload)
        if job.direction == "in":
            return self._import_product(job.instance_id, payload)
        return self._export_product(job.instance_id, payload)

    @api.model
    def _import_product(self, instance, wp):
        Binding = self.env["woo.bisync.binding"]
        binding = Binding.search([("instance_id", "=", instance.id),
                                  ("res_model", "=", "product.template"),
                                  ("external_id", "=", str(wp.get("id")))],
                                 limit=1)
        tmpl = binding and binding.resolve()
        if (tmpl and binding.odoo_write_date
                and tmpl.write_date > binding.odoo_write_date
                and instance.conflict_policy == "odoo_wins"):
            return
        vals = {"name": wp.get("name"),
                "list_price": float(wp.get("regular_price") or 0),
                "default_code": wp.get("sku"),
                "description_sale": wp.get("short_description")}
        # TODO(build): variable products -> attributes/variations endpoints
        if tmpl:
            tmpl.write(vals)
        else:
            tmpl = self.env["product.template"].create(vals)
            binding = Binding.create({
                "instance_id": instance.id, "res_model": "product.template",
                "res_id": tmpl.id, "external_id": str(wp["id"])})
        binding.write({"odoo_write_date": tmpl.write_date,
                       "external_updated_at": fields.Datetime.now()})

    @api.model
    def _export_product(self, instance, payload):
        tmpl = self.env["product.template"].browse(payload["res_id"]).exists()
        if not tmpl:
            return
        Binding = self.env["woo.bisync.binding"]
        binding = Binding.search([("instance_id", "=", instance.id),
                                  ("res_model", "=", "product.template"),
                                  ("res_id", "=", tmpl.id)], limit=1)
        body = {"name": tmpl.name, "regular_price": str(tmpl.list_price),
                "sku": tmpl.default_code or "",
                "short_description": tmpl.description_sale or ""}
        checksum = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
        if binding and binding.checksum == checksum:
            return
        if binding:
            instance._api("PUT", f"products/{binding.external_id}", body)
        else:
            res = instance._api("POST", "products", body)
            binding = Binding.create({
                "instance_id": instance.id, "res_model": "product.template",
                "res_id": tmpl.id, "external_id": str(res["id"])})
        binding.write({"checksum": checksum, "odoo_write_date": tmpl.write_date,
                       "external_updated_at": fields.Datetime.now()})

    @api.model
    def _export_stock(self, instance, payload):
        product = self.env["product.product"].browse(payload["res_id"]).exists()
        binding = product and self.env["woo.bisync.binding"].search([
            ("instance_id", "=", instance.id),
            ("res_model", "=", "product.template"),
            ("res_id", "=", product.product_tmpl_id.id)], limit=1)
        if not binding:
            return
        qty = product.with_context(warehouse_id=instance.warehouse_id.id).free_qty
        instance._api("PUT", f"products/{binding.external_id}",
                      {"manage_stock": True, "stock_quantity": int(qty)})

    @api.model
    def cron_export_stock(self):
        Job = self.env["woo.bisync.job"]
        for instance in self.env["woo.bisync.instance"].search(
                [("sync_stock", "in", ("export", "both"))]):
            for b in self.env["woo.bisync.binding"].search(
                    [("instance_id", "=", instance.id),
                     ("res_model", "=", "product.template")], limit=500):
                tmpl = b.resolve()
                if tmpl:
                    Job.enqueue(instance, "out", "stock",
                                {"res_id": tmpl.product_variant_id.id}, priority=20)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def write(self, vals):
        res = super().write(vals)
        if set(vals) & set(WooProductSync.WATCHED_FIELDS):
            Job = self.env["woo.bisync.job"]
            for instance in self.env["woo.bisync.instance"].search(
                    [("sync_products", "in", ("export", "both"))]):
                for tmpl in self:
                    Job.enqueue(instance, "out", "product", {"res_id": tmpl.id})
        return res
