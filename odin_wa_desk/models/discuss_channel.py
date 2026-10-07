from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_datetime, html2plaintext

from odoo.addons.mail.tools.discuss import Store
from odoo.addons.whatsapp_connector.models.discuss_channel import is_whatsapp_channel

#: the lowest helpdesk tier that carries the create permission on a ticket
DESK_GROUP = "helpdesk_mgmt.group_helpdesk_user_own"
#: the subject is the client's own sentence, cut to what a list can show
SUBJECT_LENGTH = 70
MANAGER_GROUP = "whatsapp_connector.group_whatsapp_manager"


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    wa_ticket_ids = fields.One2many(
        "helpdesk.ticket", "wa_channel_id", string="Service Requests",
    )
    wa_ticket_count = fields.Integer("Requests", compute="_compute_wa_ticket_count")
    odin_wa_alarm_at = fields.Datetime(
        "Unanswered Alarm Raised", readonly=True, copy=False,
        help="When the free-reply window alarm was last raised for this conversation.",
    )
    odin_wa_unanswered = fields.Boolean(
        "Unanswered", compute="_compute_odin_wa_unanswered", store=True,
        help="The client has written since anyone here last replied.",
    )

    @api.depends("wa_last_customer_message_at", "wa_last_user_message_at")
    def _compute_odin_wa_unanswered(self):
        """Stored, because a domain cannot compare two columns.

        Both timestamps are written through the ORM by the connector, so this
        recomputes with them -- which is what lets the Inbox filter on it and the
        alarm select on it rather than reading every open conversation.
        """
        for channel in self:
            said = channel.wa_last_customer_message_at
            replied = channel.wa_last_user_message_at
            channel.odin_wa_unanswered = bool(said) and (not replied or replied < said)

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
            "name": channel._wa_chat_subject(),
            "description": channel._wa_ticket_description(),
            "partner_id": partner.id,
            "user_id": self.env.user.id,
            "wa_channel_id": self.id,
            "wa_phone": channel.wa_customer_phone
            or (f"@{channel.wa_username}" if channel.wa_username else ""),
        }
        # configured per number on the account, so renaming a team or a channel
        # cannot quietly stop either from being set
        account = channel.wa_account_id
        if account.odin_desk_team_id:
            values["team_id"] = account.odin_desk_team_id.id
        if account.odin_desk_channel_id:
            values["channel_id"] = account.odin_desk_channel_id.id
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

    def _wa_chat_subject(self):
        """What the client last asked, in their own words.

        Used as a ticket's subject and as a call's summary: both are required to
        say something, and the client's own sentence is a better handle on it
        than anything generic. Falls back to the latest message of either side,
        then to the contact's name.
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
    # Schedule Call
    # ------------------------------------------------------------------

    def action_wa_schedule_call(self):
        """Put a follow-up on the client's contact instead of opening a request.

        Half of what arrives on WhatsApp is "ring me tomorrow", which needs a
        person and a date and nothing else. A ticket for that would sit in New
        on the claims board saying nothing. This opens Odoo's own activity
        dialog, so the reminder lands in the assignee's Activities, carries the
        overdue colouring every other activity has, and shows on the contact's
        timeline where the rest of the firm looks -- none of which a ticket
        stage would give it.
        """
        self.ensure_one()
        if self.channel_type != "whatsapp":
            raise UserError(self.env._("Only WhatsApp conversations have a client to call."))
        channel = self.sudo()
        partner = channel.wa_partner_id
        if not partner:
            raise UserError(self.env._(
                "This conversation has no contact yet, so there is nobody to call. "
                "Link it to a client first.",
            ))
        # the Call type carries a two-day delay, which the form's own onchange
        # turns into the due date; it has no summary, so ours survives
        call = self.env.ref("mail.mail_activity_data_call", raise_if_not_found=False)
        popup = self.env.ref("mail.mail_activity_view_form_popup")
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Schedule Call"),
            "res_model": "mail.activity",
            "views": [[popup.id, "form"]],
            "target": "new",
            "context": {
                "default_res_model": "res.partner",
                "default_res_id": partner.id,
                "default_activity_type_id": call.id if call else False,
                "default_summary": channel._wa_chat_subject(),
                "default_user_id": self.env.user.id,
                # mail.activity.create reads this to note the follow-up back here
                "wa_note_channel_id": self.id,
            },
        }

    # ------------------------------------------------------------------
    # Who the conversation belongs to
    # ------------------------------------------------------------------

    def action_wa_assign_user(self):
        """Hand the conversation to a colleague.

        The connector reserves reassignment for managers and offers it only as a
        field on the Inbox form; this is open to anyone and sits in the chat,
        because the person who reads a conversation is the one who knows who
        should answer it.

        Only users in the account's rota are offered: ``_wa_route_reopened``
        takes a conversation off anyone who is not, so assigning outside it
        would be undone by the client's next message.
        """
        self.ensure_one()
        if not self.wa_routed:
            raise UserError(self.env._(
                "This conversation has no owner to change. Lead Routing is off for "
                "its WhatsApp number.",
            ))
        account = self.sudo().wa_account_id
        if not account._wa_routing_config()._wa_eligible_users():
            raise UserError(self.env._(
                "Nobody is set up to take conversations on this number yet. Add them "
                "under WhatsApp > Configuration > WhatsApp Business Accounts, as Notify "
                "users with routing enabled.",
            ))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Assign Conversation"),
            "res_model": "odin.wa.assign",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_channel_id": self.id},
        }

    # ------------------------------------------------------------------
    # Keeping the pipeline clean
    # ------------------------------------------------------------------

    def _wa_link_lead(self, lead, owner):
        """Do not open a lead for someone who is not a prospect.

        An existing client asking for a certificate is a service request, and a
        number already marked junk is junk again next time. Either would
        otherwise add a lead on every first message, and a pipeline full of
        those stops being read -- which is how a real enquiry gets missed.
        """
        if not lead:
            partner = self.sudo().wa_partner_id
            if partner.odin_wa_junk or partner.customer_rank > 0:
                return self.env["crm.lead"]
        return super()._wa_link_lead(lead, owner)

    def action_wa_mark_junk(self):
        """Spam, a wrong number, or somebody who was never a prospect.

        Loses the lead with a reason rather than deleting it -- how much junk
        arrives is worth knowing -- flags the contact so none is opened for it
        again, archives the contact when WhatsApp is the only thing that ever
        created it, and closes the conversation. Every step is reversible.
        """
        self.ensure_one()
        if self.channel_type != "whatsapp":
            raise UserError(self.env._("Only WhatsApp conversations can be marked as junk."))
        channel = self.sudo()
        partner = channel.wa_partner_id
        done = []

        lead = channel.wa_lead_id
        if lead and lead.active and lead.won_status == "pending":
            reason = self.env.ref("odin_wa_desk.lost_reason_wa_junk", raise_if_not_found=False)
            lead.action_set_lost(**({"lost_reason_id": reason.id} if reason else {}))
            done.append(self.env._("the lead was marked lost"))

        if partner:
            partner.odin_wa_junk = True
            done.append(self.env._("the contact will not open another lead"))
            # archive only a contact WhatsApp itself created and nothing has used:
            # customer_rank and supplier_rank are what rise on a real transaction
            has_requests = self.env["helpdesk.ticket"].sudo().search_count(
                [("partner_id", "=", partner.id)], limit=1)
            if (
                partner.wa_bsuid
                and not has_requests
                and not partner.customer_rank
                and not partner.supplier_rank
                and not partner.user_ids
                and not partner.child_ids
                and not partner.parent_id
            ):
                partner.active = False
                done.append(self.env._("the contact was archived"))

        if channel.wa_status == "open":
            channel.wa_status = "closed"
            done.append(self.env._("the conversation was closed"))

        channel.with_context(wa_skip_send=True).message_post(
            body=self.env._("Marked as junk by %(who)s: %(what)s.",
                            who=self.env.user.name,
                            what=", ".join(done) or self.env._("nothing left to change")),
            message_type="notification",
        )
        return True

    # ------------------------------------------------------------------
    # Nothing waits past the free-reply window
    # ------------------------------------------------------------------

    @api.model
    def _cron_wa_unanswered(self):
        """Raise an alarm before Meta's free-reply window closes.

        Inside 24 hours of the client writing, an answer is free and immediate;
        after it only an approved template reaches them, which costs money and
        goodwill. So the deadline worth alarming on is Meta's, not an invented
        SLA -- and it is the deadline a dropped enquiry actually breaks.

        One pass per number, because the threshold is set per number. Archived
        accounts are left alone.
        """
        raised = 0
        for account in self.env["whatsapp_connector.account"].sudo().search([]):
            hours = account.odin_desk_unanswered_hours
            if hours <= 0:
                continue  # the reminder is turned off for this number
            cutoff = fields.Datetime.now() - timedelta(hours=hours)
            channels = self.sudo().search([
                ("channel_type", "=", "whatsapp"),
                ("wa_account_id", "=", account.id),
                ("wa_status", "=", "open"),
                ("odin_wa_unanswered", "=", True),
                ("wa_last_customer_message_at", "<=", cutoff),
            ])
            for channel in channels:
                raised += 1 if channel._odin_wa_raise_alarm() else 0
        return raised

    def _odin_wa_raise_alarm(self):
        """Remind whoever owns this conversation, once per unanswered message."""
        self.ensure_one()
        channel = self.sudo()
        said = channel.wa_last_customer_message_at
        if channel.odin_wa_alarm_at and channel.odin_wa_alarm_at >= said:
            return False  # already raised for this message
        partner = channel.wa_partner_id
        owner = channel.wa_assigned_user_id or channel._odin_wa_alarm_user()
        if not partner or not owner:
            return False
        todo = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        self.env["mail.activity"].sudo().create({
            "res_model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": partner.id,
            "activity_type_id": todo.id if todo else False,
            "user_id": owner.id,
            "date_deadline": fields.Date.context_today(channel.with_user(owner)),
            "summary": self.env._("Unanswered on WhatsApp: %s", channel._wa_chat_subject()),
            "note": Markup("<p>%s</p>") % self.env._(
                "%(who)s wrote at %(when)s and has had no reply. A free answer is only "
                "possible until %(until)s; after that it needs an approved template.",
                who=partner.display_name,
                when=format_datetime(self.env, said),
                until=format_datetime(self.env, channel.wa_window_expires_at)
                if channel.wa_window_expires_at else self.env._("the window closes"),
            ),
        })
        channel.odin_wa_alarm_at = fields.Datetime.now()
        channel.with_context(wa_skip_send=True).message_post(
            body=self.env._("No reply since %(when)s: a reminder was set for %(who)s.",
                            when=format_datetime(self.env, said), who=owner.name),
            message_type="notification",
        )
        return True

    def _odin_wa_alarm_user(self):
        """Who hears about an unanswered conversation that has no owner."""
        self.ensure_one()
        config = self.sudo().wa_account_id._wa_routing_config()
        if config.fallback_user_id:
            return config.fallback_user_id
        managers = self.env.ref(MANAGER_GROUP).sudo().all_user_ids.filtered(
            lambda u: u.active and not u.share,
        )
        return managers[:1]

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
