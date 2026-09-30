from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    # A customer can reach the business without a visible phone number (R16):
    # the business-scoped user ID and username identify them then.
    wa_bsuid = fields.Char("WhatsApp BSUID", index="btree_not_null", copy=False)
    wa_username = fields.Char("WhatsApp Username", copy=False)
    wa_channel_ids = fields.One2many("discuss.channel", "wa_partner_id", string="WhatsApp Conversations")
