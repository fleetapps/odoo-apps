# -*- coding: utf-8 -*-
"""Say which of our products each supplier item is, once.

The answer is remembered per supplier and item code, so the supplier's next
invoice maps itself, and it is written onto the draft bill's lines.
"""

from odoo import Command, api, fields, models


class KeEtimsPurchaseMap(models.TransientModel):
    _name = "ke.etims.purchase.map"
    _description = "Link eTIMS supplier items to products"

    purchase_id = fields.Many2one("ke.etims.purchase", required=True)
    line_ids = fields.One2many("ke.etims.purchase.map.line", "wizard_id")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        purchase = self.env["ke.etims.purchase"].browse(
            values.get("purchase_id") or self.env.context.get("default_purchase_id"))
        if purchase and "line_ids" in fields_list:
            values["line_ids"] = [Command.create({
                "purchase_line_id": line.id,
                "product_id": line.product_id.id,
            }) for line in purchase.line_ids]
        return values

    def action_apply(self):
        self.ensure_one()
        Map = self.env["ke.etims.product.map"].sudo()
        purchase = self.purchase_id
        partner = purchase.partner_id
        bill_lines = purchase.move_id.invoice_line_ids.filtered(
            lambda l: l.display_type == "product")
        for row in self.line_ids.filtered("product_id"):
            line = row.purchase_line_id
            line.product_id = row.product_id
            if partner and line.item_code:
                existing = Map.search([("partner_id", "=", partner.id),
                                       ("supplier_item_code", "=", line.item_code)], limit=1)
                if existing:
                    existing.product_id = row.product_id
                else:
                    Map.create({"partner_id": partner.id, "supplier_item_code": line.item_code,
                                "supplier_item_name": line.name, "product_id": row.product_id.id})
            if purchase.move_id.state == "draft":
                for bill_line in bill_lines.filtered(lambda l, n=line.name: l.name == n
                                                     and not l.product_id):
                    # Written together, or the product would reprice the line
                    # from its own cost and rename it.
                    bill_line.write({"product_id": row.product_id.id,
                                     "price_unit": bill_line.price_unit,
                                     "name": bill_line.name,
                                     "tax_ids": [Command.set(bill_line.tax_ids.ids)]})
        return {"type": "ir.actions.act_window_close"}


class KeEtimsPurchaseMapLine(models.TransientModel):
    _name = "ke.etims.purchase.map.line"
    _description = "Link eTIMS supplier item to a product"

    wizard_id = fields.Many2one("ke.etims.purchase.map", required=True, ondelete="cascade")
    purchase_line_id = fields.Many2one("ke.etims.purchase.line", required=True)
    item_code = fields.Char(related="purchase_line_id.item_code")
    name = fields.Char(related="purchase_line_id.name")
    quantity = fields.Float(related="purchase_line_id.quantity")
    product_id = fields.Many2one("product.product", string="Our Product")
