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
         ("variant_price_spread", "Price: variants priced differently"),
         ("other", "Other")],
        required=True, index=True, default="other")
    reference = fields.Char(
        index=True, help="Shopify reference (order number, SKU, refund id).")
    group_key = fields.Char(
        index=True, readonly=True,
        help="What counts as 'the same problem' for folding repeats together.")
    message = fields.Text(required=True)
    sale_order_id = fields.Many2one("sale.order", ondelete="set null")
    resolved = fields.Boolean(default=False)
    occurrence_count = fields.Integer(
        default=1, readonly=True, string="Times Seen",
        help="How often this same problem has recurred. One store-wide cause, "
             "such as a missing pricelist, otherwise files an identical row "
             "for every order it touches and buries everything else.")
    last_seen = fields.Datetime(
        default=fields.Datetime.now, readonly=True, index=True)

    def action_mark_resolved(self):
        self.write({"resolved": True})

    @classmethod
    def log(cls, env, instance, kind, message, reference=None, sale_order=None,
            group_key=None):
        """Record a problem, folding repeats into a single row.

        ``group_key`` is what makes two reports "the same problem". It
        defaults to the message, which is right for per-record issues (each
        names its own order). A store-wide cause should pass a stable key so
        it lands on one row with a count, instead of one row per record - the
        behaviour that made the log unreadable on a first import.
        """
        Mismatch = env["shopify.bisync.mismatch"]
        key = group_key or message
        existing = Mismatch.search([
            ("instance_id", "=", instance.id),
            ("kind", "=", kind),
            ("group_key", "=", key),
            ("resolved", "=", False),
        ], limit=1)
        if existing:
            existing.write({
                "occurrence_count": existing.occurrence_count + 1,
                "last_seen": fields.Datetime.now(),
                # Keep the newest wording: it names the most recent record,
                # which is the one someone will go and look at.
                "message": message,
                "reference": reference or existing.reference,
            })
            return existing
        return Mismatch.create({
            "instance_id": instance.id, "kind": kind, "message": message,
            "reference": reference, "group_key": key,
            "sale_order_id": sale_order.id if sale_order else False})
