from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import html2plaintext

from odoo.addons.mail.tools.discuss import Store
from odoo.addons.whatsapp_connector.models.discuss_channel import is_whatsapp_channel

#: the lowest helpdesk tier that carries the create permission on a ticket
DESK_GROUP = "helpdesk_mgmt.group_helpdesk_user_own"
#: the subject is the client's own sentence, cut to what a list can show
SUBJECT_LENGTH = 70
#: where a WhatsApp request lands unless whoever logs it moves it
DEFAULT_TEAM = "Client Service"
#: the helpdesk channel that records how the request arrived
ARRIVED_BY = "WhatsApp"


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    wa_ticket_ids = fields.One2many(
        "helpdesk.ticket", "wa_channel_id", string="Service Requests",
    )
    wa_ticket_count = fields.Integer("Requests", compute="_compute_wa_ticket_count")

    @api.depends("wa_ticket_ids")
    def _compute_wa_ticket_count(self):
        # sudo: the count belongs to whoever may read the conversation; the
        # tickets themselves are still opened with the user's own rights
        for channel in self:
            channel.wa_ticket_count = len(channel.sudo().wa_ticket_ids)

    # ------------------------------------------------------------------
    # Discuss UI
    # ------------------------------------------------------------------

    def _to_store_defaults(self, target):
        fields_ = super()._to_store_defaults(target)
        if target.is_current_user(self.env):
            # the header action is hidden rather than left to fail for someone
            # without the service desk
            can_create = self.env.user.has_group(DESK_GROUP)
            fields_.append(Store.Attr(
                "wa_can_create_ticket", can_create, predicate=is_whatsapp_channel,
            ))
        return fields_

    # ------------------------------------------------------------------
    # Create Ticket
    # ------------------------------------------------------------------

    def action_wa_create_ticket(self):
        """Open a service request from this conversation.

        The ticket records the commitment -- who asked, for what, who owns it --
        and links back here. The conversation stays the single copy of what was
        said: a transcript in the ticket would be one message out of date as
        soon as the client writes again, and two versions of a client's words
        is worse than one.
        """
        self.ensure_one()
        if self.channel_type != "whatsapp":
            raise UserError(self.env._(
                "Only WhatsApp conversations can be logged as a service request.",
            ))
        if not self.env.user.has_group(DESK_GROUP):
            raise AccessError(self.env._("You do not have access to the service desk."))
        channel = self.sudo()
        partner = channel.wa_partner_id
        if not partner:
            raise UserError(self.env._(
                "This conversation has no contact yet, so there is nobody to open a "
                "request for.",
            ))
        values = {
            "name": channel._wa_ticket_subject(),
            "description": channel._wa_ticket_description(),
            "partner_id": partner.id,
            "user_id": self.env.user.id,
            "wa_channel_id": self.id,
            "wa_phone": channel.wa_customer_phone
            or (f"@{channel.wa_username}" if channel.wa_username else ""),
        }
        team = self.env["helpdesk.ticket.team"].search([("name", "=", DEFAULT_TEAM)], limit=1) \
            or self.env["helpdesk.ticket.team"].search([], order="sequence, id", limit=1)
        if team:
            values["team_id"] = team.id
        arrived = self.env["helpdesk.ticket.channel"].search([("name", "=", ARRIVED_BY)], limit=1)
        if arrived:
            values["channel_id"] = arrived.id
        ticket = self.env["helpdesk.ticket"].create(values)
        channel.with_context(wa_skip_send=True).message_post(
            # Markup: the translation is escaped around the link
            body=self.env._("Service request %s opened from this conversation.",
                            ticket._get_html_link()),
            message_type="notification",
        )
        return ticket._wa_ticket_action()

    def action_wa_open_tickets(self):
        """The requests already opened from this conversation."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Service Requests"),
            "res_model": "helpdesk.ticket",
            "domain": [("wa_channel_id", "=", self.id)],
            "views": [(False, "list"), (False, "form")],
            "context": {},
        }

    def _wa_ticket_subject(self):
        """What the client last asked, in their own words.

        The subject is required, and their sentence is a better handle on the
        request than anything generic. Falls back to the latest message of
        either side, then to the contact's name.
        """
        self.ensure_one()
        messages = self.message_ids.filtered(
            lambda m: m.message_type in ("whatsapp_message", "comment") and m.body,
        ).sorted("id")
        from_client = messages.filtered(lambda m: m.author_id == self.wa_partner_id)
        latest = (from_client or messages)[-1:]
        text = " ".join(html2plaintext(latest.body or "").split()) if latest else ""
        if not text:
            return self.env._("WhatsApp request from %s", self.wa_partner_id.display_name)
        return text[:SUBJECT_LENGTH - 1] + "…" if len(text) > SUBJECT_LENGTH else text

    def _wa_ticket_description(self):
        """Where the request came from, and nothing else.

        ``description`` is required on a ticket; this fills it with provenance
        rather than with a copy of the conversation.
        """
        self.ensure_one()
        return Markup("<p>%s</p>") % self.env._(
            "Logged from the WhatsApp conversation with %(name)s (%(phone)s).",
            name=self.wa_partner_id.display_name or "",
            phone=self.wa_customer_phone
            or (f"@{self.wa_username}" if self.wa_username else self.env._("no number")),
        )

    # ------------------------------------------------------------------
    # Link to Client
    # ------------------------------------------------------------------

    def action_wa_link_client(self):
        """Point this conversation at the client's real contact.

        A number WhatsApp has not seen before gets a brand new contact, which is
        a duplicate whenever the client is already on file under another number
        or spelling. Create Lead and Create Ticket both write to whatever
        contact the conversation carries, so this is the action that has to come
        before either of them.
        """
        self.ensure_one()
        if self.channel_type != "whatsapp":
            raise UserError(self.env._("Only WhatsApp conversations have a client to link."))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Link to Client"),
            "res_model": "odin.wa.link.client",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_channel_id": self.id},
        }
