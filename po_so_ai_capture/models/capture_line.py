# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PoCaptureLine(models.Model):
    _name = "po.capture.line"
    _description = "Captured PO Line"
    _order = "capture_id, id"

    capture_id = fields.Many2one("po.capture", required=True,
                                 ondelete="cascade")
    partner_id = fields.Many2one(related="capture_id.partner_id", store=True)
    customer_code = fields.Char(string="Customer SKU",
                                help="The part number AS PRINTED on their PO.")
    description = fields.Char()
    quantity = fields.Float(default=1.0)
    price_unit = fields.Float(string="Their Price",
                              help="Printed price - shown for comparison, "
                                   "Odoo pricelist governs the SO by default.")
    extract_confidence = fields.Float(help="LLM's own confidence in this line.")
    product_id = fields.Many2one("product.product", string="Matched Product")
    match_confidence = fields.Float()
    match_method = fields.Selection(
        [("alias", "Learned alias"), ("sku", "Internal ref"),
         ("barcode", "Barcode"), ("fuzzy", "Name similarity"),
         ("manual", "Human pick")], readonly=True)
    line_confidence = fields.Float(compute="_compute_line_confidence",
                                   store=True)
    price_delta_pct = fields.Float(compute="_compute_price_delta", store=True,
        help="Their printed price vs our list price - flags price disputes early.")

    @api.depends("extract_confidence", "match_confidence")
    def _compute_line_confidence(self):
        for l in self:
            l.line_confidence = min(l.extract_confidence or 0.0,
                                    l.match_confidence or 0.0)

    @api.depends("price_unit", "product_id")
    def _compute_price_delta(self):
        for l in self:
            lp = l.product_id.lst_price
            l.price_delta_pct = ((l.price_unit - lp) / lp) if lp and l.price_unit else 0.0

    def rematch(self):
        Matcher = self.env["po.capture.matcher"]
        for l in self:
            product, conf, method = Matcher.match_product(
                l.partner_id, l.customer_code, l.description)
            l.write({"product_id": product.id or False,
                     "match_confidence": conf, "match_method": method})

    def write(self, vals):
        # human correction detection: manual product pick = full confidence
        if "product_id" in vals and not self.env.context.get("capture_auto"):
            vals.setdefault("match_confidence", 1.0)
            vals.setdefault("match_method", "manual")
        return super().write(vals)

    def _teach_aliases(self):
        """Persist confirmed (partner, customer_code) -> product mappings.
        THE MOAT: accuracy compounds per customer with every processed PO."""
        Alias = self.env["po.capture.sku.alias"]
        for l in self.filtered(lambda l: l.product_id and l.customer_code
                               and l.partner_id):
            alias = Alias.search([
                ("partner_id", "=", l.partner_id.id),
                ("customer_code", "=ilike", l.customer_code.strip())], limit=1)
            if alias:
                if alias.product_id != l.product_id:
                    alias.product_id = l.product_id  # newest confirmation wins
                alias.hit_count += 1
            else:
                Alias.create({"partner_id": l.partner_id.id,
                              "customer_code": l.customer_code.strip(),
                              "product_id": l.product_id.id, "hit_count": 1})
