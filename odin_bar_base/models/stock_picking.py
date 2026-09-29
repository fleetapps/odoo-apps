from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bar_business_date = fields.Date(
        "Trading day",
        copy=False,
        index="btree_not_null",
        help="Business day this transfer belongs to. The POS importer sets it on "
        "sales deliveries, which are posted after the day has ended. Bar counts "
        "place these transfers by trading day instead of by posting time.",
    )
