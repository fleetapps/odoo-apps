from odoo import _, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    def action_odin_online_share(self):
        """Send the client the online quote page, filled in for them."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Send online quote link"),
            "res_model": "odin.medical.quote.online.share",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_lead_id": self.id},
        }
