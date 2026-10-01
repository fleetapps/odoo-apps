from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    wa_bsuid = fields.Char("WhatsApp BSUID", index="btree_not_null", copy=False)
    wa_username = fields.Char("WhatsApp Username", copy=False)
    wa_channel_ids = fields.One2many("discuss.channel", "wa_lead_id", string="WhatsApp Conversations")
    wa_channel_count = fields.Integer("WhatsApp", compute="_compute_wa_channel_stats")
    wa_last_message_at = fields.Datetime("Last WhatsApp Message", compute="_compute_wa_channel_stats")

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
