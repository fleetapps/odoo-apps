# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Configuration mapping tables:

- ``shopify.bisync.location.map``: Shopify location <-> Odoo warehouse, the
  backbone of multi-location inventory sync (``inventorySetQuantities`` needs
  a location id per quantity).
- ``shopify.bisync.carrier.map``: Shopify shipping-line code/title ->
  ``delivery.carrier`` for imported orders.
"""
from odoo import fields, models


class ShopifyLocationMap(models.Model):
    _name = "shopify.bisync.location.map"
    _description = "Shopify Location Mapping"
    _check_company_auto = True

    _uniq_location = models.Constraint(
        "UNIQUE(instance_id, shopify_location_id)",
        "This Shopify location is already mapped.")

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(
        related="instance_id.company_id", store=True, index=True)
    shopify_location_id = fields.Char(required=True)
    shopify_location_name = fields.Char(readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", check_company=True,
        help="Leave empty to exclude this Shopify location from stock sync.")
    stock_sync = fields.Boolean(
        default=True, string="Sync Stock",
        help="Export/import quantities for this location.")


class ShopifyCarrierMap(models.Model):
    _name = "shopify.bisync.carrier.map"
    _description = "Shopify Carrier Mapping"
    _check_company_auto = True

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(
        related="instance_id.company_id", store=True, index=True)
    shopify_code = fields.Char(
        required=True, string="Shopify Code / Title",
        help="Matched case-insensitively against the shipping line's code, "
             "then its title.")
    carrier_id = fields.Many2one(
        "delivery.carrier", required=True, check_company=True)

    def match(self, instance, shipping_line):
        """Resolve a Shopify shipping line dict to a delivery.carrier."""
        code = (shipping_line.get("code") or "").strip().lower()
        title = (shipping_line.get("title") or "").strip().lower()
        for row in self.search([("instance_id", "=", instance.id)]):
            if (row.shopify_code or "").strip().lower() in (code, title):
                return row.carrier_id
        return instance.fallback_carrier_id
