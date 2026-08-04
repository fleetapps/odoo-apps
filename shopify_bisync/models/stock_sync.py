# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Inventory sync (spec A1).

Export (default): ``free_qty`` per mapped warehouse -> GraphQL
``inventorySetQuantities`` (absolute "available" quantities,
``ignoreCompareQuantity``). One job per variant; the multi-location mapping
table (``shopify.bisync.location.map``) decides which Shopify location gets
which warehouse's quantity.

Import (optional two-way): ``inventory_levels/update`` webhook -> inventory
adjustment on the mapped warehouse's stock location (quant ``inventory_mode``
+ ``action_apply_inventory``). Echo-safe: an inbound level equal to the
current free qty is a no-op.

Real-time trigger: ``stock.move._action_done`` enqueues one job per bound
product (debounced by the queue's pending-duplicate check); the hourly sweep
cron remains the safety net underneath.
"""
import json
import logging
import uuid

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

#: API 2026-04 removed ``ignoreCompareQuantity``/``compareQuantity`` and made
#: BOTH the per-quantity ``changeFromQuantity`` and the ``@idempotent``
#: directive mandatory at runtime (they are not marked required in the schema,
#: so omitting them fails only when the mutation actually runs).
#: ``changeFromQuantity: null`` is the documented opt-out of the compare-and-
#: swap check, i.e. the exact replacement for ``ignoreCompareQuantity: true``:
#: Odoo remains the source of truth and sets the absolute quantity.
#: https://shopify.dev/changelog/finalizing-compare-and-swap-redesign-for-inventory-set-quantities
#: https://shopify.dev/changelog/making-idempotency-mandatory-for-inventory-adjustments-and-refund-mutations
INVENTORY_SET = """
mutation inventorySetQuantities($input: InventorySetQuantitiesInput!,
                                $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup { createdAt }
    userErrors { field message }
  }
}"""


class StockSync(models.AbstractModel):
    _name = "shopify.bisync.stock.sync"
    _description = "Stock Sync Engine"

    @api.model
    def process_job(self, job):
        payload = json.loads(job.payload_json or "{}")
        if job.direction == "in":
            self._import_level(job.instance_id, payload)
        else:
            self._export_product_stock(job.instance_id, payload,
                                       job._idempotency_key())

    # -------------------------------------------------------------- export --
    @api.model
    def _stock_locations(self, instance):
        """Mapping rows that take part in stock sync; falls back to a virtual
        row on the instance warehouse when the user mapped nothing (yet the
        binding knows no Shopify location id then -> rows must be fetched)."""
        return instance.location_map_ids.filtered(
            lambda m: m.stock_sync and m.warehouse_id)

    @api.model
    def _export_product_stock(self, instance, payload, idempotency_key=None):
        if instance.sync_stock not in ("export", "both"):
            return
        product = self.env["product.product"].browse(
            payload["res_id"]).exists()
        binding = self.env["shopify.bisync.binding"].get(
            self.env, instance, "product.product", record=product)
        if not product or not binding or not binding.inventory_item_id:
            return
        quantities = []
        for row in self._stock_locations(instance):
            # 'warehouse_id' is the Odoo 19 context key; 'warehouse' kept so
            # the same code backports to 17/18 unchanged.
            qty = product.with_context(
                warehouse_id=row.warehouse_id.id,
                warehouse=row.warehouse_id.id)[instance.stock_quantity_source]
            # "Never send zero" leaves Shopify's figure untouched rather than
            # writing 0, for merchants who also sell this stock elsewhere.
            if instance.stock_skip_zero and qty <= 0:
                continue
            quantities.append({
                "inventoryItemId": instance.gid(
                    "InventoryItem", binding.inventory_item_id),
                "locationId": instance.gid(
                    "Location", row.shopify_location_id),
                "quantity": int(qty),
                # Explicit null = opt out of the compare-and-swap check.
                "changeFromQuantity": None,
            })
        if not quantities:
            _logger.info(
                "shopify_bisync: no synced location mapping on %s - stock of "
                "%s not exported. Fetch & map locations on the store form.",
                instance.name, product.display_name)
            return
        data = instance.graphql(INVENTORY_SET, {
            "input": {
                # Shopify accepts only "available" or "on_hand" here; the
                # field's selection is limited to those two for that reason.
                "name": instance.stock_target_name,
                "reason": "correction",
                "quantities": quantities,
            },
            "idempotencyKey": idempotency_key or str(uuid.uuid4()),
        })
        instance.check_user_errors(
            data.get("inventorySetQuantities") or {}, "inventorySetQuantities")
        instance.last_export_stock = fields.Datetime.now()

    # -------------------------------------------------------------- import --
    @api.model
    def _import_level(self, instance, payload):
        """inventory_levels/update webhook: {inventory_item_id, location_id,
        available}. Two-way stock only; applied as an inventory adjustment."""
        if instance.sync_stock not in ("import", "both"):
            return
        binding = self.env["shopify.bisync.binding"].search([
            ("instance_id", "=", instance.id),
            ("res_model", "=", "product.product"),
            ("inventory_item_id", "=", str(payload.get("inventory_item_id")))],
            limit=1)
        row = instance.location_map_ids.filtered(
            lambda m: m.stock_sync and m.warehouse_id
            and m.shopify_location_id == str(payload.get("location_id")))[:1]
        product = binding and binding.resolve()
        if not product or not row or payload.get("available") is None:
            return
        available = float(payload["available"])
        location = row.warehouse_id.lot_stock_id
        current = product.with_context(
            warehouse_id=row.warehouse_id.id,
            warehouse=row.warehouse_id.id).free_qty
        if abs(current - available) < 1e-6:
            return  # echo of our own export: no-op
        Quant = self.env["stock.quant"].with_context(inventory_mode=True)
        quant = Quant.search([("product_id", "=", product.id),
                              ("location_id", "=", location.id)], limit=1)
        if not quant:
            quant = Quant.create({"product_id": product.id,
                                  "location_id": location.id})
        # Adjust so the warehouse's free qty lands on Shopify's number even
        # when stock is spread over child locations.
        quant.inventory_quantity = quant.quantity + (available - current)
        quant.action_apply_inventory()

    # ---------------------------------------------------------------- crons -
    @api.model
    def cron_export_stock(self):
        """Hourly sweep: safety net under the real-time move trigger."""
        Job = self.env["shopify.bisync.job"]
        for instance in self.env["shopify.bisync.instance"].search(
                [("sync_stock", "in", ("export", "both"))]):
            bindings = self.env["shopify.bisync.binding"].search(
                [("instance_id", "=", instance.id),
                 ("res_model", "=", "product.product"),
                 ("inventory_item_id", "!=", False)])
            for binding in bindings:
                Job.enqueue(instance, "out", "stock",
                            {"res_id": binding.res_id}, priority=20,
                            lock_key=f"stock:{binding.res_id}")


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        Job = self.env["shopify.bisync.job"]
        Binding = self.env["shopify.bisync.binding"]
        products = moves.product_id
        for instance in self.env["shopify.bisync.instance"].sudo().search(
                [("sync_stock", "in", ("export", "both"))]):
            for product in products:
                if Binding.get(self.env, instance, "product.product",
                               record=product):
                    Job.enqueue(instance, "out", "stock",
                                {"res_id": product.id}, priority=15,
                                lock_key=f"stock:{product.id}")
        return moves
