from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    odin_bar_id = fields.Many2one(
        "odin.bar",
        string="Bar Desk bar",
        help="Bar this login works for in the Bar Desk. Staff signed in on it "
        "can only act for this bar. For managers it is the bar the Desk opens on.",
    )
    odin_bar_ids = fields.Many2many(
        "odin.bar",
        "res_users_odin_bar_desk_rel",
        "user_id",
        "bar_id",
        string="Other Desk bars",
        help="More bars this login may open, e.g. a roving supervisor's phone. "
        "The Desk then shows a bar switcher. Leave empty on a bar's own tablet.",
    )

    def _odin_desk_bars(self):
        """Bars a staff login may act for."""
        self.ensure_one()
        user = self.sudo()
        return (user.odin_bar_id | user.odin_bar_ids).filtered("active")
