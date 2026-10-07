from odoo import fields, models


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    wa_channel_id = fields.Many2one(
        "discuss.channel", "WhatsApp Conversation", readonly=True, copy=False,
        index="btree_not_null", ondelete="set null",
        help="The WhatsApp conversation this request was logged from.",
    )

    def action_wa_open_chat(self):
        """Back to the conversation, to answer the client who asked."""
        self.ensure_one()
        if not self.wa_channel_id:
            return False
        return self.wa_channel_id.sudo()._get_access_action()

    def _wa_ticket_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": self.id,
            "views": [(False, "form")],
            "target": "current",
        }
