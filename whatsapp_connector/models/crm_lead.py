from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    wa_bsuid = fields.Char("WhatsApp BSUID", index="btree_not_null", copy=False)
    wa_username = fields.Char("WhatsApp Username", copy=False)
    wa_channel_ids = fields.One2many("discuss.channel", "wa_lead_id", string="WhatsApp Conversations")
    wa_channel_count = fields.Integer("WhatsApp", compute="_compute_wa_channel_stats")
    wa_last_message_at = fields.Datetime("Last WhatsApp Message", compute="_compute_wa_channel_stats")
    wa_referral = fields.Json(
        "WhatsApp Ad Referral", copy=False,
        help="Click-to-WhatsApp ad or post the conversation started from (R21).",
    )

    @api.depends("wa_channel_ids.wa_last_message_at")
    def _compute_wa_channel_stats(self):
        for lead in self:
            channels = lead.sudo().wa_channel_ids
            lead.wa_channel_count = len(channels)
            dates = [d for d in channels.mapped("wa_last_message_at") if d]
            lead.wa_last_message_at = max(dates) if dates else False

    def action_wa_open_conversations(self):
        """SPEC.md §30: the lead's WhatsApp conversations."""
        self.ensure_one()
        return self.env["discuss.channel"]._wa_conversations_action([("wa_lead_id", "=", self.id)])

    def write(self, vals):
        result = super().write(vals)
        if "user_id" in vals and not self.env.context.get("wa_owner_sync"):
            # R31: a new salesperson, by hand or by CRM's own assignment, takes
            # the lead's routed conversations with them
            self.sudo()._wa_sync_conversation_owner()
        return result

    def _wa_sync_conversation_owner(self):
        for lead in self:
            for channel in lead.wa_channel_ids.filtered(lambda c: c.wa_routed and c.wa_status == "open"):
                if channel.wa_assigned_user_id != lead.user_id:
                    channel._wa_assign(lead.user_id, reason="lead")

    def _merge_dependences(self, opportunities):
        """R31: conversations follow the lead that survives a merge."""
        super()._merge_dependences(opportunities)
        channels = opportunities.sudo().wa_channel_ids
        if channels:
            channels.write({"wa_lead_id": self.id})
            self.sudo()._wa_sync_conversation_owner()
