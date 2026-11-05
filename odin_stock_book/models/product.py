from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    stock_book_unlimited = fields.Boolean(
        string="Never short in Stock Book",
        help="For things a recipe uses but nobody counts, such as tap water: the Stock Book "
        "does not stop a filling or batch because of them.")
