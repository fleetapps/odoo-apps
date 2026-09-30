from odoo import api, fields, models

ACTIVITY_KINDS = [
    ("stock_out", "Stock out"),
    ("count", "Count submitted"),
    ("ack", "Delivery confirmed"),
    ("dispute", "Delivery disputed"),
    ("send", "Sent to bar"),
    ("receive", "Received from supplier"),
    ("dispute_accept", "Dispute accepted"),
    ("dispute_reject", "Dispute rejected"),
    ("pin_fail", "Wrong PIN"),
    ("ask", "Asked for stock"),
    ("ask_none", "Could not send"),
]


class OdinBarActivity(models.Model):
    """One row per Bar Desk action: who did what, where and when.

    It is the Desk's audit trail, feeds the bar's timeline and the stock-out
    log, and carries the client request id that makes retries safe: a request
    replayed with the same id finds its row and returns it instead of posting
    again.
    """

    _name = "odin.bar.activity"
    _description = "Bar Desk Activity"
    _order = "date desc, id desc"

    bar_id = fields.Many2one("odin.bar", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="bar_id.company_id", store=True, index=True)
    kind = fields.Selection(ACTIVITY_KINDS, required=True, index=True)
    uuid = fields.Char(
        "Request ID",
        readonly=True,
        copy=False,
        help="Id the device generated for this action. Retries with the same "
        "id return this record instead of posting again.",
    )
    date = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    business_date = fields.Date("Trading day", index=True)
    employee_id = fields.Many2one("hr.employee", string="Staff", index="btree_not_null")
    user_id = fields.Many2one("res.users", string="Login", default=lambda self: self.env.user)
    reason_id = fields.Many2one("odin.bar.reason", string="Reason", index="btree_not_null")
    dest_bar_id = fields.Many2one("odin.bar", string="To bar", index="btree_not_null")
    partner_id = fields.Many2one("res.partner", string="Supplier")
    member_ref = fields.Char("Member")
    note = fields.Char()
    summary = fields.Char()
    amount = fields.Monetary("Value", currency_field="currency_id", help="Value at cost.")
    currency_id = fields.Many2one(related="company_id.currency_id")
    picking_ids = fields.One2many("stock.picking", "bar_activity_id", string="Transfers")
    scrap_ids = fields.One2many("stock.scrap", "bar_activity_id", string="Scraps")
    count_id = fields.Many2one("odin.bar.count", string="Count", index="btree_not_null")
    request_id = fields.Many2one("odin.bar.request", string="Request", index="btree_not_null")
    bill_id = fields.Many2one("account.move", string="Supplier bill", index="btree_not_null")
    source_picking_id = fields.Many2one(
        "stock.picking", string="Delivery", index="btree_not_null"
    )

    _uuid_uniq = models.UniqueIndex(
        "(uuid) WHERE uuid IS NOT NULL", "This Bar Desk request was already processed."
    )

    @api.depends("kind", "bar_id", "summary")
    def _compute_display_name(self):
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for activity in self:
            activity.display_name = f"{kinds.get(activity.kind, '')} · {activity.bar_id.name or ''}"

    def action_open_records(self):
        """Open what this action created."""
        self.ensure_one()
        if self.count_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "odin.bar.count",
                "res_id": self.count_id.id,
                "views": [(False, "form")],
            }
        if self.scrap_ids:
            action = self.env["ir.actions.act_window"]._for_xml_id("stock.action_stock_scrap")
            action["domain"] = [("id", "in", self.scrap_ids.ids)]
            action["context"] = {}
            return action
        pickings = self.picking_ids | self.source_picking_id
        action = self.env["ir.actions.act_window"]._for_xml_id("stock.action_picking_tree_all")
        action["domain"] = [("id", "in", pickings.ids)]
        action["context"] = {}
        return action
