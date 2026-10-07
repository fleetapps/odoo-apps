from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
from odoo.tools.misc import format_datetime

#: how many of the conversation's most recent messages go into the description
TRANSCRIPT_LENGTH = 15
#: the ticket subject is the client's own words, cut to something readable
SUMMARY_LENGTH = 70
#: the team a WhatsApp request goes to unless the user moves it
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
        for channel in self:
            channel.wa_ticket_count = len(channel.sudo().wa_ticket_ids)

    # ------------------------------------------------------------------
    # Log a conversation as a service request
    # ------------------------------------------------------------------

    def action_wa_create_ticket(self):
        """Open a helpdesk ticket from this conversation.

        Logging a WhatsApp request has to cost one click or it does not get
        logged at all, so the client's own words are carried over rather than
        retyped, and the ticket opens straight away for the team and category
        to be set while the conversation is still in front of the user.
        """
        self.ensure_one()
        if self.channel_type != "whatsapp":
            raise UserError(self.env._(
                "Only WhatsApp conversations can be logged as a service request.",
            ))
        channel = self.sudo()
        partner = channel.wa_partner_id
        if not partner:
            raise UserError(self.env._(
                "This conversation has no contact yet, so there is nobody to open the "
                "request for.",
            ))
        values = {
            "name": self._wa_ticket_summary(),
            "description": self._wa_ticket_description(),
            "partner_id": partner.id,
            "user_id": self.env.user.id,
            "wa_channel_id": self.id,
        }
        team = self._wa_ticket_team()
        if team:
            values["team_id"] = team.id
        arrived_by = self.env["helpdesk.ticket.channel"].search(
            [("name", "=", ARRIVED_BY)], limit=1,
        )
        if arrived_by:
            values["channel_id"] = arrived_by.id
        ticket = self.env["helpdesk.ticket"].create(values)
        # Markup: the translation is escaped around the link
        link = ticket._get_html_link()
        channel.with_context(wa_skip_send=True).message_post(
            body=self.env._("Service request %s opened from this conversation.", link),
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
            "context": {"default_wa_channel_id": self.id,
                        "default_partner_id": self.sudo().wa_partner_id.id},
        }

    # ------------------------------------------------------------------
    # What the ticket says
    # ------------------------------------------------------------------

    def _wa_ticket_messages(self):
        """The conversation's real messages, oldest first: no audit notes."""
        self.ensure_one()
        messages = self.sudo().message_ids.filtered(
            lambda m: m.message_type in ("whatsapp_message", "comment") and m.body,
        ).sorted("id")
        return messages[-TRANSCRIPT_LENGTH:]

    def _wa_ticket_summary(self):
        """The subject: what the client last asked, in their words.

        Falls back to the latest message of either side, then to the contact's
        name, because the subject is required and an empty one is worse than an
        approximate one.
        """
        self.ensure_one()
        channel = self.sudo()
        messages = self._wa_ticket_messages()
        from_client = messages.filtered(lambda m: m.author_id == channel.wa_partner_id)
        latest = (from_client or messages)[-1:]
        text = " ".join(html2plaintext(latest.body or "").split()) if latest else ""
        if not text:
            return self.env._("WhatsApp request from %s", channel.wa_partner_id.display_name)
        return text[:SUMMARY_LENGTH - 1] + "…" if len(text) > SUMMARY_LENGTH else text

    def _wa_ticket_description(self):
        """The recent conversation, as plain text turned into safe HTML.

        Message bodies are rendered by WhatsApp and by Odoo users, so they are
        flattened to text and re-escaped rather than pasted into the ticket.
        """
        self.ensure_one()
        channel = self.sudo()
        customer = channel.wa_partner_id
        rows = []
        for message in self._wa_ticket_messages():
            text = " ".join(html2plaintext(message.body or "").split())
            if not text:
                continue
            who = customer.display_name if message.author_id == customer else (
                message.author_id.display_name or self.env._("Us")
            )
            when = format_datetime(self.env, message.date, dt_format="short")
            rows.append("<li><b>%s</b> <span class='text-muted'>%s</span><br/>%s</li>" % (
                escape(who), escape(when), escape(text),
            ))
        heading = self.env._("From the WhatsApp conversation with %s",
                            customer.display_name or channel.wa_customer_contact or "")
        if not rows:
            return Markup("<p>%s</p>") % heading
        return Markup("<p><b>%s</b></p><ul>%s</ul>") % (heading, Markup("".join(rows)))

    def _wa_ticket_team(self):
        """Where a WhatsApp request lands by default.

        Client Service if it exists, otherwise the first team, otherwise none:
        a missing team must not stop the request being logged.
        """
        Team = self.env["helpdesk.ticket.team"]
        return Team.search([("name", "=", DEFAULT_TEAM)], limit=1) or Team.search(
            [], order="sequence, id", limit=1,
        )
