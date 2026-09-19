# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    threecx_call_count = fields.Integer(compute="_compute_threecx_call_count")

    def _compute_threecx_call_count(self):
        counts = dict(self.env["crm.3cx.call"]._read_group([("lead_id", "in", self.ids)], ["lead_id"], ["__count"]))
        for lead in self:
            lead.threecx_call_count = counts.get(lead, 0)

    def action_view_threecx_calls(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("crm_3cx.action_crm_3cx_call")
        action["domain"] = [("lead_id", "=", self.id)]
        action["context"] = {"default_lead_id": self.id}
        return action
