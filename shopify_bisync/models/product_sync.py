# -*- coding: utf-8 -*-
"""Product two-way sync with conflict policy.

Odoo->Shopify export triggers: product template write() on watched fields.
Shopify->Odoo import triggers: products/update webhook -> job queue.
Conflict: both changed since last fingerprints -> instance.conflict_policy.
"""
import hashlib
import json
from odoo import api, fields, models


class ProductSync(models.AbstractModel):
    _name = "shopify.bisync.product.sync"
    _description = "Product Sync Engine"

    WATCHED_FIELDS = ("name", "list_price", "default_code", "barcode",
                      "description_sale", "active")

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.direction == "in":
            self._import_product(job.instance_id, payload)
        else:
            self._export_product(job.instance_id, payload)

    # ---------------------------------------------------------------- import
    @api.model
    def _import_product(self, instance, sp):
        Binding = self.env["shopify.bisync.binding"]
        binding = Binding.search([
            ("instance_id", "=", instance.id),
            ("res_model", "=", "product.template"),
            ("external_id", "=", str(sp.get("id")))], limit=1)
        tmpl = binding and binding.resolve()
        odoo_changed = bool(
            tmpl and binding.odoo_write_date and
            tmpl.write_date > binding.odoo_write_date)
        if odoo_changed and instance.conflict_policy == "odoo_wins":
            return  # keep Odoo version; next export overwrites Shopify
        vals = {
            "name": sp.get("title"),
            "description_sale": sp.get("body_html"),
        }
        variants = sp.get("variants") or []
        if len(variants) == 1:
            vals.update({"list_price": float(variants[0].get("price") or 0),
                         "default_code": variants[0].get("sku")})
        # TODO(build): multi-variant mapping via product.template.attribute.*
        if tmpl:
            tmpl.write(vals)
        else:
            tmpl = self.env["product.template"].create(vals)
            binding = Binding.create({
                "instance_id": instance.id, "res_model": "product.template",
                "res_id": tmpl.id, "external_id": str(sp["id"])})
        binding.write({
            "external_updated_at": fields.Datetime.now(),
            "odoo_write_date": tmpl.write_date,
            "checksum": hashlib.sha1(
                json.dumps(vals, sort_keys=True, default=str).encode()).hexdigest(),
        })

    # ---------------------------------------------------------------- export
    @api.model
    def _export_product(self, instance, payload):
        tmpl = self.env["product.template"].browse(payload["res_id"]).exists()
        if not tmpl:
            return
        Binding = self.env["shopify.bisync.binding"]
        binding = Binding.search([
            ("instance_id", "=", instance.id),
            ("res_model", "=", "product.template"),
            ("res_id", "=", tmpl.id)], limit=1)
        body = {"product": {
            "title": tmpl.name,
            "body_html": tmpl.description_sale or "",
            "variants": [{"price": str(tmpl.list_price),
                          "sku": tmpl.default_code or "",
                          "barcode": tmpl.barcode or ""}],
        }}
        checksum = hashlib.sha1(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        if binding and binding.checksum == checksum:
            return  # no-op, skip API call entirely
        if binding:
            instance.api_call("PUT", f"products/{binding.external_id}.json", body)
        else:
            res = instance.api_call("POST", "products.json", body)
            binding = Binding.create({
                "instance_id": instance.id, "res_model": "product.template",
                "res_id": tmpl.id, "external_id": str(res["product"]["id"])})
        binding.write({"checksum": checksum,
                       "odoo_write_date": tmpl.write_date,
                       "external_updated_at": fields.Datetime.now()})


class ProductTemplate(models.Model):
    _inherit = "product.template"

    def write(self, vals):
        res = super().write(vals)
        if set(vals) & set(ProductSync.WATCHED_FIELDS):
            Job = self.env["shopify.bisync.job"]
            for instance in self.env["shopify.bisync.instance"].search(
                    [("sync_products", "in", ("export", "both"))]):
                for tmpl in self:
                    Job.enqueue(instance, "out", "product", {"res_id": tmpl.id})
        return res
