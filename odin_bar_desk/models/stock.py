from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    bar_employee_id = fields.Many2one(
        "hr.employee", string="Bar staff", copy=False, index="btree_not_null"
    )
    bar_reason_id = fields.Many2one(
        "odin.bar.reason", string="Stock-out reason", copy=False, index="btree_not_null"
    )
    bar_member_ref = fields.Char("Member", copy=False, help="Member on an unpaid bill.")
    bar_activity_id = fields.Many2one(
        "odin.bar.activity", string="Desk action", copy=False, index="btree_not_null"
    )
    bar_ack_state = fields.Selection(
        [("not_checked", "Not checked"), ("confirmed", "Confirmed"), ("disputed", "Disputed")],
        string="Bar check",
        compute="_compute_bar_ack_state",
        store=True,
        readonly=False,
        copy=False,
        index="btree_not_null",
        help="Whether the receiving bar checked this delivery. Checking is optional: "
        "stock is at the bar once the transfer is done, and a shortage in an "
        "unchecked delivery counts against the bar.",
    )
    bar_billed = fields.Boolean(
        "Already billed",
        copy=False,
        help="Items still to come on a supplier invoice already billed: receiving them bills nothing.",
    )
    bar_ack_employee_id = fields.Many2one("hr.employee", string="Checked by", copy=False)
    bar_ack_date = fields.Datetime("Checked at", copy=False)
    bar_dispute_origin_id = fields.Many2one(
        "stock.picking",
        string="Disputed delivery",
        copy=False,
        index="btree_not_null",
        help="Delivery this correction was raised against.",
    )
    bar_dispute_ids = fields.One2many(
        "stock.picking", "bar_dispute_origin_id", string="Disputes", copy=False
    )
    bar_dispute_state = fields.Selection(
        [("pending", "Pending"), ("accepted", "Accepted"), ("rejected", "Rejected")],
        string="Dispute",
        compute="_compute_bar_dispute_state",
    )

    @api.depends("state", "location_id", "location_dest_id")
    def _compute_bar_ack_state(self):
        bar_locations = set(
            self.env["odin.bar"].sudo().search([("kind", "=", "bar")]).location_id.ids
        )
        for picking in self:
            if picking.bar_ack_state:
                picking.bar_ack_state = picking.bar_ack_state
            elif (
                picking.state == "done"
                and picking.location_dest_id.id in bar_locations
                and picking.location_id.usage == "internal"
                and not picking.bar_dispute_origin_id
            ):
                picking.bar_ack_state = "not_checked"
            else:
                picking.bar_ack_state = False

    @api.depends("state", "bar_dispute_origin_id")
    def _compute_bar_dispute_state(self):
        for picking in self:
            if not picking.bar_dispute_origin_id:
                picking.bar_dispute_state = False
            elif picking.state == "done":
                picking.bar_dispute_state = "accepted"
            elif picking.state == "cancel":
                picking.bar_dispute_state = "rejected"
            else:
                picking.bar_dispute_state = "pending"


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
