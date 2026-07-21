# -*- coding: utf-8 -*-
"""Stock & price export. Debounced: quants changes enqueue at most one job
per product per cron window (checksum short-circuit handles the rest)."""
import json
from odoo import api, models


class StockSync(models.AbstractModel):
    _name = "shopify.bisync.stock.sync"
    _description = "Stock/Price Sync Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        instance = job.instance_id
        product = self.env["product.product"].browse(payload["res_id"]).exists()
        if not product:
            return
        qty = product.with_context(
            warehouse_id=instance.warehouse_id.id).free_qty
        # inventory_levels/set.json requires inventory_item_id + location_id:
        # cache both on the binding at first export. VERIFY-ON-BUILD against
        # current Inventory API (GraphQL inventorySetQuantities is the new path).
        binding = self.env["shopify.bisync.binding"].search([
            ("instance_id", "=", instance.id),
            ("res_model", "=", "product.product"),
            ("res_id", "=", product.id)], limit=1)
        if not binding:
            return
        instance.api_call("POST", "inventory_levels/set.json", {
            "location_id": payload.get("location_id"),
            "inventory_item_id": binding.external_id,
            "available": int(qty),
        })

    @api.model
    def cron_export_stock(self):
        """Periodic sweep as the safety net under real-time triggers."""
        Job = self.env["shopify.bisync.job"]
        for instance in self.env["shopify.bisync.instance"].search(
                [("sync_stock", "in", ("export", "both"))]):
            bindings = self.env["shopify.bisync.binding"].search(
                [("instance_id", "=", instance.id),
                 ("res_model", "=", "product.product")], limit=500)
            for b in bindings:
                Job.enqueue(instance, "out", "stock", {"res_id": b.res_id}, priority=20)
