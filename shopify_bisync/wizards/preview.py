# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Dry run: show exactly what an export would change on Shopify.

Every connector in this category asks the merchant to press Sync and find out
afterwards. The checksum machinery that already makes an unchanged export a
zero-API-call no-op is also, read the other way round, a change detector - so
the same code can answer "what would happen?" before anything happens.

Nothing here writes to Shopify. The only action that does is the explicit
Push button, which enqueues an ordinary export job.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ShopifyPreview(models.TransientModel):
    _name = "shopify.bisync.preview"
    _description = "Preview Shopify Changes"

    product_tmpl_id = fields.Many2one(
        "product.template", required=True, string="Product", readonly=True)
    instance_id = fields.Many2one(
        "shopify.bisync.instance", required=True, string="Store",
        readonly=True)
    line_ids = fields.One2many(
        "shopify.bisync.preview.line", "preview_id", readonly=True)
    summary = fields.Char(readonly=True)
    has_changes = fields.Boolean(readonly=True)

    @api.model
    def open_for(self, tmpl):
        """Build a preview for the first store this product is (or would be)
        synced to."""
        Instance = self.env["shopify.bisync.instance"]
        binding = self.env["shopify.bisync.binding"].search([
            ("res_model", "=", "product.template"),
            ("res_id", "=", tmpl.id)], limit=1)
        instance = binding.instance_id or Instance.search(
            [("sync_products", "in", ("export", "both"))], limit=1)
        if not instance:
            raise UserError(_(
                "No Shopify store is set to export products, so there is "
                "nothing to compare this product against."))
        rows = self.env["shopify.bisync.product.sync"].diff_against_shopify(
            instance, tmpl)
        wizard = self.create({
            "product_tmpl_id": tmpl.id,
            "instance_id": instance.id,
            "has_changes": bool(rows),
            "summary": (_("%s difference(s) would be pushed.", len(rows))
                        if rows else
                        _("Shopify already matches Odoo - an export right now "
                          "would send nothing.")),
            "line_ids": [fields.Command.create({
                "name": row["field"],
                "odoo_value": row["odoo"],
                "shopify_value": row["shopify"],
            }) for row in rows],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Preview Shopify Changes"),
            "res_model": "shopify.bisync.preview",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_push(self):
        """Queue the export the preview just described."""
        self.ensure_one()
        self.env["shopify.bisync.job"].enqueue(
            self.instance_id, "out", "product",
            {"res_id": self.product_tmpl_id.id}, priority=25,
            lock_key=f"product:{self.product_tmpl_id.id}")
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "sticky": False,
                       "message": _("Export queued for %s.",
                                    self.product_tmpl_id.display_name)}}


class ShopifyPreviewLine(models.TransientModel):
    _name = "shopify.bisync.preview.line"
    _description = "Preview Shopify Change Line"

    preview_id = fields.Many2one(
        "shopify.bisync.preview", required=True, ondelete="cascade")
    name = fields.Char(string="What", readonly=True)
    odoo_value = fields.Char(string="Odoo (would send)", readonly=True)
    shopify_value = fields.Char(string="Shopify (now)", readonly=True)
