from odoo import fields, models


class MailMessage(models.Model):
    _inherit = "mail.message"

    # The key Odoo Enterprise's WhatsApp app uses, handled by Odoo 19 Community
    # core for notifications (SPEC.md §12.1, R9). On uninstall the messages
    # become comments, so the history is kept (R33).
    message_type = fields.Selection(
        selection_add=[("whatsapp_message", "WhatsApp")],
        ondelete={"whatsapp_message": "set default"},
    )
    wa_message_ids = fields.One2many("whatsapp_connector.message", "mail_message_id")
