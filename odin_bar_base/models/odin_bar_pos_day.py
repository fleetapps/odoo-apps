from odoo import api, fields, models


class OdinBarPosDay(models.Model):
    """One row per bar and trading day whose POS sales are posted to stock.

    The POS importer creates these through ``odin.bar._mark_pos_posted`` and
    removes them through ``odin.bar._unmark_pos_posted``. A manager can add
    one by hand for a day without a POS report, e.g. the club was closed.
    Adding or removing a row by hand moves no stock.
    """

    _name = "odin.bar.pos.day"
    _description = "Bar POS Day"
    _order = "business_date desc, bar_id"

    bar_id = fields.Many2one("odin.bar", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="bar_id.company_id", store=True, index=True)
    business_date = fields.Date("Trading day", required=True, index=True)
    no_sales = fields.Boolean(help="The bar did not trade that day.")
    source = fields.Char(help="Reference of the POS import that posted this day.")
    posted_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    posted_by_id = fields.Many2one(
        "res.users", string="Posted by", default=lambda self: self.env.user, readonly=True
    )
    note = fields.Text()
    picking_ids = fields.Many2many("stock.picking", compute="_compute_picking_ids")
    picking_count = fields.Integer(compute="_compute_picking_ids")

    _bar_day_uniq = models.Constraint(
        "UNIQUE(bar_id, business_date)",
        "The POS import for this bar and trading day is already marked as posted.",
    )

    @api.depends("bar_id", "business_date")
    def _compute_display_name(self):
        for day in self:
            day.display_name = f"{day.bar_id.name} · {day.business_date or ''}"

    @api.depends("bar_id", "business_date")
    def _compute_picking_ids(self):
        Picking = self.env["stock.picking"]
        for day in self:
            if day.bar_id and day.business_date:
                day.picking_ids = Picking.search(
                    [
                        ("bar_business_date", "=", day.business_date),
                        ("location_id", "=", day.bar_id.location_id.id),
                    ]
                )
            else:
                day.picking_ids = Picking
            day.picking_count = len(day.picking_ids)

    def action_view_pickings(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("stock.action_picking_tree_all")
        action["domain"] = [("id", "in", self.picking_ids.ids)]
        action["context"] = {}
        return action
