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
    bar_manual_move = fields.Boolean(
        "Moved by hand",
        copy=False,
        help="Stock moved between the club's locations (or out of the club) and "
        "logged by hand, e.g. from the paper sheet. It can carry a trading day like "
        "POS sales, but it is never counted as sales.",
    )
