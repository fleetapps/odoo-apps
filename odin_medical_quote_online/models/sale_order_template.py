from odoo import fields, models


class SaleOrderTemplate(models.Model):
    _inherit = "sale.order.template"

    odin_online = fields.Boolean(
        string="Show in online quotes", default=True,
        help="Offered on the public online quote page, to anyone pricing "
        "cover for their family. Untick a plan to quote it only through an "
        "agent.",
    )
