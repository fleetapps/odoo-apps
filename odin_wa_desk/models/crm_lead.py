from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    odin_wa_junk = fields.Boolean(
        related="partner_id.odin_wa_junk", string="Marked Junk", readonly=True,
        help="Its contact was marked as spam or a wrong number from WhatsApp.",
    )

    def action_odin_wa_unjunk(self):
        """Undo a junk mark.

        Conditioned on the contact's flag rather than on the lead being archived:
        every lost lead is archived, and offering "Not Junk" on one lost as too
        expensive would be nonsense.
        """
        self.ensure_one()
        partner = self.sudo().partner_id
        partner.write({"odin_wa_junk": False, "active": True})
        self.action_unarchive()
        self.write({"lost_reason_id": False})
        return True
