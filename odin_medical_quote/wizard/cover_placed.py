from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RenewalPlaced(models.TransientModel):
    """The insurer has confirmed the cover: its policy number and the day it
    starts, so the renewal is dated right and can be found by number."""

    _name = "odin.renewal.placed"
    _description = "Cover placed with the insurer"

    comparison_id = fields.Many2one("odin.quote.comparison", required=True, ondelete="cascade")
    renewal_id = fields.Many2one("odin.renewal")
    policy_number = fields.Char(required=True)
    cover_start = fields.Date(string="Cover starts", required=True,
                              default=fields.Date.context_today)
    renewal_date = fields.Date(compute="_compute_renewal_date", string="Renews on")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        comparison = self.env["odin.quote.comparison"].browse(values.get("comparison_id") or [])
        renewal = comparison.odin_next_renewal_id
        if renewal:
            values["renewal_id"] = renewal.id
            if renewal.policy_number:
                values["policy_number"] = renewal.policy_number
            if renewal.cover_start:
                values["cover_start"] = renewal.cover_start
        return values

    @api.depends("cover_start", "renewal_id.period_months")
    def _compute_renewal_date(self):
        for wizard in self:
            months = wizard.renewal_id.period_months or 12
            wizard.renewal_date = wizard.cover_start + relativedelta(months=months) \
                if wizard.cover_start else False

    def action_confirm(self):
        self.ensure_one()
        comparison = self.comparison_id
        if comparison.state != "accepted":
            raise UserError(_("Mark the plan the client accepted first."))
        renewal = self.renewal_id
        if renewal:
            renewal.write({
                "policy_number": self.policy_number,
                "cover_start": self.cover_start,
                "renewal_date": self.renewal_date,
                "start_to_confirm": False,
            })
        activity_type = comparison._odin_place_cover_type()
        pending = comparison.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)
        if pending:
            pending.action_feedback(feedback=_("Cover placed: policy %s", self.policy_number))
        else:
            comparison.message_post(body=_("Cover placed: policy %s", self.policy_number))
        return {"type": "ir.actions.act_window_close"}
