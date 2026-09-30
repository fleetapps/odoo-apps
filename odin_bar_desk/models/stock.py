from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bar_employee_id = fields.Many2one(
        "hr.employee", string="Logged by", copy=False, index="btree_not_null"
    )
    bar_reason_id = fields.Many2one(
        "odin.bar.reason", string="Out of the club", copy=False, index="btree_not_null"
    )
    bar_member_ref = fields.Char("Member", copy=False, help="Member on an unpaid bill.")
    bar_activity_id = fields.Many2one(
        "odin.bar.activity", string="Desk action", copy=False, index="btree_not_null"
    )
    bar_billed = fields.Boolean(
        "Already billed",
        copy=False,
        help="Items still to come on a supplier invoice already billed: receiving them bills nothing.",
    )


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    bar_employee_id = fields.Many2one(
        "hr.employee", string="Bar staff", copy=False, index="btree_not_null"
    )
    bar_reason_id = fields.Many2one(
        "odin.bar.reason", string="Stock-out reason", copy=False, index="btree_not_null"
    )
    bar_activity_id = fields.Many2one(
        "odin.bar.activity", string="Desk action", copy=False, index="btree_not_null"
    )


class StockMove(models.Model):
    _inherit = "stock.move"

    bar_count_id = fields.Many2one(
        "odin.bar.count", string="Bar count", copy=False, index="btree_not_null"
    )
    bar_variance_reason_id = fields.Many2one(
        "odin.bar.variance.reason",
        string="Variance reason",
        copy=False,
        index="btree_not_null",
        help="Why the count differed, on the adjustment a count approval posted.",
    )
