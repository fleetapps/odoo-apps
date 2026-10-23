from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    odin_wa_no_autoreply = fields.Boolean(
        "No WhatsApp auto-replies", copy=False,
        help="Set when the customer replied STOP: people still answer them, the automatic "
        "replies do not.")
