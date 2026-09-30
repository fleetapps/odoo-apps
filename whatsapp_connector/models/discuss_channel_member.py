from odoo import fields, models


class DiscussChannelMember(models.Model):
    _inherit = "discuss.channel.member"

    # A Notify user who has not taken part is only a listener: once someone
    # replies, listeners are muted for 15 days (SPEC.md §10, R6). Users who post
    # or are invited are participants and keep their notifications.
    wa_participant = fields.Boolean("Takes part in the WhatsApp conversation", default=True)
