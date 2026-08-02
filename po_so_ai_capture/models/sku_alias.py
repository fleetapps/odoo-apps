# -*- coding: utf-8 -*-
from odoo import fields, models


class SkuAlias(models.Model):
    """Per-customer part-number memory. Grows automatically from confirmed
    captures; also editable/importable so onboarding can seed it from the
    customer's price file on day one."""
    _name = "po.capture.sku.alias"
    _description = "Customer SKU Alias"
    _rec_name = "customer_code"

    partner_id = fields.Many2one("res.partner", required=True, index=True,
                                 ondelete="cascade")
    customer_code = fields.Char(required=True, index=True)
    product_id = fields.Many2one("product.product", required=True,
                                 ondelete="cascade")
    hit_count = fields.Integer(default=0, help="Times confirmed by a human.")

    _uniq_alias = models.Constraint(
        "UNIQUE(partner_id, customer_code)",
        "This customer already has a mapping for that code.")
