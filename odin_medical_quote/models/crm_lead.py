from odoo import _, api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    odin_comparison_ids = fields.One2many(
        "odin.quote.comparison", "opportunity_id", string="Medical quotes")
    odin_comparison_count = fields.Integer(compute="_compute_odin_comparison_count")

    @api.depends("odin_comparison_ids")
    def _compute_odin_comparison_count(self):
        for lead in self:
            lead.odin_comparison_count = len(lead.odin_comparison_ids)

    def action_set_lost(self, **additional_values):
        """A lost opportunity's medical quotes are declined and their open
        quotations cancelled, whether it was lost from CRM or from the quote."""
        result = super().action_set_lost(**additional_values)
        self.sudo().odin_comparison_ids.filtered("odin_from_builder")._odin_decline()
        return result

    def action_odin_medical_quote(self):
        """Quote this lead in Add Medical Quote, the client already filled in."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "odin_medical_quote",
            "name": _("Add Medical Quote"),
            "params": {"lead_id": self.id},
        }

    def action_odin_view_comparisons(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "odin_client_quote.action_quote_comparison")
        action["domain"] = [("opportunity_id", "=", self.id)]
        action["context"] = {"create": False}
        if len(self.odin_comparison_ids) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = self.odin_comparison_ids.id
        return action
