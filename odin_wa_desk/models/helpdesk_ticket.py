from odoo import fields, models
from odoo.exceptions import UserError

MANAGER_GROUP = "whatsapp_connector.group_whatsapp_manager"


class HelpdeskTicket(models.Model):
    _inherit = "helpdesk.ticket"

    wa_channel_id = fields.Many2one(
        "discuss.channel", "WhatsApp Conversation", readonly=True, copy=False,
        index="btree_not_null", ondelete="set null",
        help="The WhatsApp conversation this request was logged from.",
    )
    wa_phone = fields.Char(
        "WhatsApp Number", readonly=True, copy=False,
        help="The number the request came from, as it stood when the request was logged.",
    )

    def action_wa_open_chat(self):
        """Open the conversation this request came from.

        Only a conversation's members may read it, so whoever works the ticket
        is added to it -- they need the client's replies, not a dead link. Lead
        Routing reserves membership for managers, and there they are told to ask
        the owner instead of being added behind the owner's back.
        """
        self.ensure_one()
        channel = self.wa_channel_id.sudo()
        if not channel:
            raise UserError(self.env._("This request did not come from WhatsApp."))
        partner = self.env.user.partner_id
        if partner not in channel.channel_member_ids.partner_id:
            # has_group on self.env.user, never on the sudo channel: _wa_is_manager
            # answers True for any superuser environment
            if channel.wa_routed and not self.env.user.has_group(MANAGER_GROUP):
                owner = channel.wa_assigned_user_id
                raise UserError(
                    self.env._("This conversation is assigned to %s. Ask them to add "
                               "you to it.", owner.name) if owner
                    else self.env._("Nobody owns this conversation yet: take it from the "
                                    "WhatsApp Inbox first."),
                )
            channel._add_members(partners=partner, post_joined_message=False)
        return channel._get_access_action()

    def _wa_ticket_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": self.id,
            "views": [(False, "form")],
            "target": "current",
        }
