# -*- coding: utf-8 -*-
"""Binding pattern: one row per (odoo record <-> external id <-> instance).
Modeled after the well-established OCA connector binding architecture so data
stays clean with multiple stores. Every synced entity gets a binding row with
sync fingerprints for conflict detection.
"""
from odoo import fields, models


class ShopifyBinding(models.Model):
    _name = "shopify.bisync.binding"
    _description = "Shopify Binding"
    _rec_name = "external_id"

    instance_id = fields.Many2one("shopify.bisync.instance", required=True,
                                  ondelete="cascade", index=True)
    res_model = fields.Char(required=True, index=True)   # product.template, product.product, sale.order, res.partner
    res_id = fields.Integer(required=True, index=True)
    external_id = fields.Char(required=True, index=True) # Shopify numeric/GID
    external_updated_at = fields.Datetime(
        help="Shopify's updated_at at last sync - conflict detection input.")
    odoo_write_date = fields.Datetime(
        help="Odoo write_date at last sync - conflict detection input.")
    checksum = fields.Char(help="Hash of last synced payload; skip no-op syncs.")

    _sql_constraints = [
        ("uniq_binding", "unique(instance_id, res_model, res_id)",
         "Record already bound to this store."),
        ("uniq_external", "unique(instance_id, res_model, external_id)",
         "External id already bound."),
    ]

    def resolve(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id).exists()
