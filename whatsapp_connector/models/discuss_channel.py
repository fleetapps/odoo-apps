from datetime import timedelta

from odoo import api, fields, models

# Meta's customer service window (SPEC.md §28, R34): free-form messages only
# within 24 hours of the customer's last message or call.
CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)


def is_whatsapp_channel(channel):
    return channel.channel_type == "whatsapp"


class DiscussChannel(models.Model):
    """A WhatsApp conversation is a Discuss channel of type 'whatsapp' (SPEC.md §12.1).

    The type key is the one Odoo Enterprise's WhatsApp app uses, which Odoo 19
    Community core already handles (R9). 'cascade' on uninstall is what
    im_livechat does for its own type; the alternative is open decision D5.
    """

    _inherit = "discuss.channel"

    channel_type = fields.Selection(
        selection_add=[("whatsapp", "WhatsApp Conversation")],
        ondelete={"whatsapp": "cascade"},
    )
    wa_account_id = fields.Many2one(
        # restrict: an account with conversations is archived, never deleted
        "whatsapp_connector.account", "WhatsApp Account", index="btree_not_null", ondelete="restrict",
    )
    # Identity (R16): the business-scoped user ID first; wa_customer_key is the
    # BSUID when known, otherwise "wa:<WhatsApp ID>" for conversations from
    # before BSUIDs. One open conversation per account and key.
    wa_customer_key = fields.Char(index="btree_not_null", copy=False)
    wa_bsuid = fields.Char("Business-scoped User ID", index="btree_not_null", copy=False)
    wa_parent_bsuid = fields.Char("Parent BSUID", copy=False)
    wa_id = fields.Char("WhatsApp ID", index="btree_not_null", copy=False)
    wa_customer_phone = fields.Char("Customer Phone", copy=False, help="E.164, when WhatsApp provides it.")
    wa_username = fields.Char("WhatsApp Username", copy=False)
    wa_partner_id = fields.Many2one("res.partner", "Customer", index="btree_not_null", ondelete="set null")
    wa_lead_id = fields.Many2one("crm.lead", "Lead", index="btree_not_null", ondelete="set null")
    wa_assigned_user_id = fields.Many2one("res.users", "Assigned to", index="btree_not_null", ondelete="set null")
    wa_assigned_team_id = fields.Many2one("crm.team", "Sales Team", ondelete="set null")
    wa_status = fields.Selection(
        [("open", "Open"), ("closed", "Closed")], "Conversation Status", default="open", copy=False,
    )
    wa_last_message_at = fields.Datetime("Last Message", copy=False)
    wa_last_customer_message_at = fields.Datetime("Last Customer Message", copy=False)
    wa_last_user_message_at = fields.Datetime("Last Reply", copy=False)
    wa_window_expires_at = fields.Datetime(
        "Free-form Messages Until", compute="_compute_wa_window_expires_at",
        help="End of Meta's 24-hour customer service window. After it, only templates can be sent.",
    )
    wa_referral = fields.Json("Ad Referral", copy=False, help="Click-to-WhatsApp ad data, when present.")
    wa_message_ids = fields.One2many("whatsapp_connector.message", "channel_id", string="WhatsApp Messages")

    _wa_open_conversation_unique = models.UniqueIndex(
        "(wa_account_id, wa_customer_key) WHERE channel_type = 'whatsapp' AND wa_status = 'open'",
        "This customer already has an open WhatsApp conversation on this account.",
    )

    @api.depends("wa_last_customer_message_at")
    def _compute_wa_window_expires_at(self):
        for channel in self:
            last = channel.wa_last_customer_message_at
            channel.wa_window_expires_at = last + CUSTOMER_SERVICE_WINDOW if last else False

    def _wa_window_open(self):
        self.ensure_one()
        return bool(self.wa_window_expires_at and self.wa_window_expires_at > fields.Datetime.now())

    def _types_allowing_seen_infos(self):
        return super()._types_allowing_seen_infos() + ["whatsapp"]

    def _types_allowing_unfollow(self):
        return super()._types_allowing_unfollow() + ["whatsapp"]
