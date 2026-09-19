# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    threecx_call_count = fields.Integer(compute="_compute_threecx_call_count")

    def _compute_threecx_call_count(self):
        counts = dict(self.env["crm.3cx.call"]._read_group([("partner_id", "in", self.ids)], ["partner_id"], ["__count"]))
        for partner in self:
            partner.threecx_call_count = counts.get(partner, 0)

    def action_view_threecx_calls(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("crm_3cx.action_crm_3cx_call")
        action["domain"] = [("partner_id", "=", self.id)]
        action["context"] = {"default_partner_id": self.id}
        return action
