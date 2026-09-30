# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
from markupsafe import Markup

from odoo import fields, models

from .crm_3cx_call import ANSWERED, CALLBACK_PREFIX
from .res_config_settings import PARAM_MISSED_ACTIVITY, dial_url, param_flag


class ResPartner(models.Model):
    _inherit = "res.partner"

    threecx_call_count = fields.Integer(compute="_compute_threecx_call_count")
    threecx_renewal_date = fields.Date(
        "Renewal Date",
        help="When this customer's policy or contract comes up for renewal. A renewal "
        "lead is created ahead of it (Settings > 3CX) and 3CX shows the date when they call.",
    )
    threecx_policy_ref = fields.Char("Policy / Contract Ref")

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

    def action_threecx_call(self):
        self.ensure_one()
        url = dial_url(self.env, self.phone_sanitized or self.phone)
        if not url:
            return False
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def _threecx_assignee(self, agent=None):
        self.ensure_one()
        return self.user_id or (agent if agent else None) or self.env.ref("base.user_admin")

    def _threecx_after_call(self, call, agent=None):
        """Call-back on a miss, closed by the next conversation. Returns the activity scheduled, if any."""
        self.ensure_one()
        Call = self.env["crm.3cx.call"]
        if call.call_type in ANSWERED:
            Call._close_call_activities(self, "Reached on a 3CX call")
        elif call.call_type == "missed" and param_flag(self.env, PARAM_MISSED_ACTIVITY):
            if not Call._open_call_activities(self):
                return Call._schedule_call(
                    self, CALLBACK_PREFIX + (call.number or self.display_name), user=self._threecx_assignee(agent),
                    note=Markup("<p>Missed call%s.</p>") % (" for " + agent.name if agent else ""),
                )
        return None

    def _threecx_open_opportunity(self):
        """The most recently worked open lead or opportunity of this person or
        their company: what an agent needs in front of them when the phone rings."""
        self.ensure_one()
        partners = self | self.commercial_partner_id
        return self.env["crm.lead"].sudo().search(
            [("partner_id", "in", partners.ids), ("won_status", "=", "pending")], order="write_date desc", limit=1,
        )
