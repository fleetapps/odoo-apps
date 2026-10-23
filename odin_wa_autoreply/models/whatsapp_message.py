from odoo import fields, models


class WhatsappMessage(models.Model):
    _inherit = "whatsapp_connector.message"

    odin_auto = fields.Boolean(
        "Auto-reply", readonly=True, copy=False,
        help="Sent automatically to a customer nobody had answered yet.")
