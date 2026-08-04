# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Mismatch log: the "never drop anything silently" ledger.

Every order line that fell through the resolution ladder, refund without an
invoice, unmappable currency, etc. lands here with enough context for a human
to fix the catalog and re-run the job.
"""
from odoo import fields, models


class ShopifyMismatch(models.Model):
    _name = "shopify.bisync.mismatch"
    _description = "Shopify Sync Mismatch"
    _order = "id desc"

    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, ondelete="cascade",
        index=True)
    company_id = fields.Many2one(
        related="instance_id.company_id", store=True, index=True)
    kind = fields.Selection(
        [("line_unmatched", "Order line: no product matched"),
         ("refund_no_invoice", "Refund: no posted invoice"),
         ("currency_unmapped", "Currency: no pricelist"),
         ("fulfillment_gap", "Fulfillment: no open fulfillment order"),
         ("edit_delivered", "Order edit on delivered line"),
         ("match_ambiguous", "Match before create: ambiguous candidate"),
         ("image_gallery", "Images: gallery not synced"),
         ("other", "Other")],
        required=True, index=True, default="other")
    reference = fields.Char(
        index=True, help="Shopify reference (order number, SKU, refund id).")
    message = fields.Text(required=True)
    sale_order_id = fields.Many2one("sale.order", ondelete="set null")
    resolved = fields.Boolean(default=False)

    def action_mark_resolved(self):
        self.write({"resolved": True})

    @classmethod
    def log(cls, env, instance, kind, message, reference=None, sale_order=None):
        return env["shopify.bisync.mismatch"].create({
            "instance_id": instance.id, "kind": kind, "message": message,
            "reference": reference,
            "sale_order_id": sale_order.id if sale_order else False})
