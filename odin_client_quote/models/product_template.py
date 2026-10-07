from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    odin_instalment_interest = fields.Boolean(
        string="Instalment financing charge",
        help="Tick for products that carry the interest or fee of an instalment "
        "plan rather than cover itself. Client reports leave these out of the "
        "premium they quote, so an instalment schedule is never charged "
        "interest on interest.",
    )
