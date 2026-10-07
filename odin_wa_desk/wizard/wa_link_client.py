from odoo import fields, models
from odoo.exceptions import UserError


class WaLinkClient(models.TransientModel):
    """Move a WhatsApp conversation onto the client's real contact."""

    _name = "odin.wa.link.client"
    _description = "Link a WhatsApp Conversation to a Client"

    channel_id = fields.Many2one(
        "discuss.channel", "Conversation", required=True, readonly=True, ondelete="cascade",
    )
    current_partner_id = fields.Many2one(
        related="channel_id.wa_partner_id", string="Currently linked to", readonly=True,
    )
    wa_phone = fields.Char(
        related="channel_id.wa_customer_phone", string="WhatsApp number", readonly=True,
    )
    partner_id = fields.Many2one(
        "res.partner", "The client", required=True,
        help="The contact this conversation really belongs to.",
    )

    def action_link(self):
        """Carry the conversation and its WhatsApp identity to the real contact.

        The contact being left behind is not deleted or archived: that is not
        reversible, and Contacts has a merge tool made for it. What does move is
        the WhatsApp identity, so the client's next message resolves to the real
        contact, and the records opened from this conversation, so none of them
        is left pointing at a duplicate.
        """
        self.ensure_one()
        channel = self.channel_id.sudo()
        if channel.channel_type != "whatsapp":
            raise UserError(self.env._("Only WhatsApp conversations have a client to link."))
        old = channel.wa_partner_id
        new = self.partner_id.sudo()
        if new == old:
            raise UserError(self.env._("That is already the contact on this conversation."))

        # _wa_find_or_create matches the business-scoped id first, then the
        # number: moving them is what makes the next message land on `new`
        carried = {}
        if channel.wa_bsuid and new.wa_bsuid != channel.wa_bsuid:
            carried["wa_bsuid"] = channel.wa_bsuid
        if channel.wa_username and not new.wa_username:
            carried["wa_username"] = channel.wa_username
        if channel.wa_customer_phone and not new.phone:
            carried["phone"] = channel.wa_customer_phone
        if carried:
            new.write(carried)
        if old:
            # leaving the id on both would make row order decide which contact
            # a future message resolves to
            old.write({"wa_bsuid": False, "wa_username": False})

        channel.write({"wa_partner_id": new.id, "name": new.display_name})

        moved = []
        tickets = channel.wa_ticket_ids
        if tickets:
            tickets.write({"partner_id": new.id})
            moved.append(self.env._("%s service request(s)", len(tickets)))
        lead = channel.wa_lead_id
        if lead:
            lead.write({"partner_id": new.id})
            moved.append(self.env._("the linked lead"))

        note = self.env._(
            "Conversation moved from %(old)s to %(new)s by %(who)s.",
            old=old.display_name or self.env._("no contact"),
            new=new.display_name, who=self.env.user.name,
        )
        if moved:
            note += " " + self.env._("Also moved: %s.", ", ".join(moved))
        if old:
            note += " " + self.env._(
                "%s was left in place -- merge it from Contacts if it is a duplicate.",
                old.display_name,
            )
        channel.with_context(wa_skip_send=True).message_post(
            body=note, message_type="notification",
        )
        new.message_post(
            body=self.env._("WhatsApp conversation linked to this contact."),
            # sudo posts as OdooBot unless the real author is named
            author_id=self.env.user.partner_id.id,
            message_type="comment", subtype_xmlid="mail.mt_note",
        )
        return {"type": "ir.actions.act_window_close"}
