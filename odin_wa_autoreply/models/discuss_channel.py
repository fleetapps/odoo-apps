"""A WhatsApp conversation waiting for a person, and what is done about it.

Each customer message starts (or continues) a wait. An agent's reply -- typed
in Discuss, or a template they send -- ends it. While it lasts, the scheduled
action sends the auto-reply when it is due, tells the team after the working
hours set on the account, and gives the escalation people a to-do before
WhatsApp's 24-hour window closes.
"""

import logging
from datetime import timedelta
from urllib.parse import urlencode

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.tools import plaintext2html

from . import classifier

_logger = logging.getLogger(__name__)

KINDS = [
    (classifier.MEDICAL, "Medical quote link"),
    (classifier.MOTOR, "Motor quote link"),
    (classifier.BOTH, "Both quote links"),
    (classifier.OTHER, "An advisor will reply"),
    (classifier.SERVICE, "Service: attending to it"),
    ("client", "Existing client: their agent"),
]
#: one auto-reply per conversation per day, and none while a person is talking
QUIET = timedelta(hours=24)
BATCH = 200


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    odin_wait_since = fields.Datetime(
        "Waiting since", copy=False, index="btree_not_null",
        help="The customer's first message no agent has answered yet.")
    odin_autoreply_due = fields.Datetime("Auto-reply due", copy=False, index="btree_not_null")
    odin_autoreply_at = fields.Datetime("Auto-replied", copy=False)
    odin_autoreply_kind = fields.Selection(KINDS, "Auto-reply", copy=False)
    odin_team_alerted = fields.Boolean(copy=False)
    odin_manager_alerted = fields.Boolean(copy=False)

    # -- the wait ------------------------------------------------------------

    def _wa_receive(self, account, message, contact, identity):
        wa_message = super()._wa_receive(account, message, contact, identity)
        if wa_message and wa_message.direction == "inbound":
            self._odin_customer_wrote(wa_message)
        return wa_message

    def _wa_after_user_message(self, author):
        if self.env.context.get("odin_wa_auto"):
            # Our own reply: not a person's, so nobody is muted and the wait goes on.
            self.sudo().write({"wa_last_message_at": fields.Datetime.now()})
            return None
        result = super()._wa_after_user_message(author)
        self.sudo().write({"odin_wait_since": False, "odin_autoreply_due": False,
                           "odin_team_alerted": False, "odin_manager_alerted": False})
        return result

    def _odin_customer_wrote(self, wa_message):
        self.ensure_one()
        channel = self.sudo()
        now = fields.Datetime.now()
        partner = channel.wa_partner_id
        if classifier.is_stop(wa_message.body):
            if partner and not partner.odin_wa_no_autoreply:
                partner.odin_wa_no_autoreply = True
                channel.with_context(wa_skip_send=True).message_post(
                    body=_("The customer asked not to get automatic replies."),
                    message_type="notification")
            return
        if not channel.odin_wait_since:
            channel.odin_wait_since = wa_message.wa_timestamp or now
        if channel.odin_autoreply_due or not channel._odin_may_autoreply(now):
            return
        account = channel.wa_account_id
        due = now if not account._odin_is_open(now) else now + timedelta(
            minutes=account.odin_autoreply_delay)
        channel.odin_autoreply_due = due
        self.env.ref("odin_wa_autoreply.ir_cron_wa_autoreply").sudo()._trigger(at=due)

    def _odin_is_staff(self):
        return bool(self.wa_partner_id.user_ids.filtered(lambda u: not u.share))

    def _odin_may_autoreply(self, now):
        """Whether this conversation may get an auto-reply now."""
        self.ensure_one()
        account = self.wa_account_id
        partner = self.wa_partner_id
        last_reply = self.wa_last_user_message_at
        return bool(
            account.odin_autoreply
            and self.wa_status == "open"
            and self.odin_wait_since
            and not partner.odin_wa_no_autoreply
            and not self._odin_is_staff()
            and not (self.odin_autoreply_at and now - self.odin_autoreply_at < QUIET)
            # a person is already talking with them
            and not (last_reply and now - last_reply < QUIET)
            and self._wa_window_open()
            and self._wa_recipient()
        )

    # -- the scheduled action ----------------------------------------------

    @api.model
    def _odin_autoreply_cron(self):
        now = fields.Datetime.now()
        base = [("channel_type", "=", "whatsapp"), ("wa_account_id.odin_autoreply", "=", True)]
        for channel in self.sudo().search(
                base + [("odin_autoreply_due", "<=", now)], order="odin_autoreply_due", limit=BATCH):
            channel._odin_run(channel._odin_autoreply)
        for channel in self.sudo().search(
                base + [("odin_wait_since", "!=", False), ("wa_status", "=", "open"),
                        "|", ("odin_team_alerted", "=", False), ("odin_manager_alerted", "=", False)],
                order="odin_wait_since", limit=BATCH):
            channel._odin_run(channel._odin_alerts)

    def _odin_run(self, step):
        """One conversation at a time: one failure must not stop the others."""
        try:
            with self.env.cr.savepoint():
                step()
        except Exception:
            _logger.exception("WhatsApp auto-reply failed on conversation %s", self.id)

    # -- the auto-reply ------------------------------------------------------

    def _odin_waiting_text(self):
        """What the customer wrote while waiting."""
        self.ensure_one()
        messages = self.env["whatsapp_connector.message"].sudo().search([
            ("channel_id", "=", self.id), ("direction", "=", "inbound"),
            ("wa_timestamp", ">=", self.odin_wait_since),
        ], order="wa_timestamp, id")
        return " ".join(m.body for m in messages if m.body)

    def _odin_autoreply(self):
        self.ensure_one()
        now = fields.Datetime.now()
        self.odin_autoreply_due = False
        if not self._odin_may_autoreply(now):
            return
        kind, hints = classifier.classify(self._odin_waiting_text())
        agent = self.env["res.users"]
        if kind != classifier.SERVICE:
            agent = self._odin_client_agent()
            if agent:
                kind = "client"
        lead = self._odin_lead()
        links = {}
        if kind in (classifier.MEDICAL, classifier.MOTOR, classifier.BOTH):
            links = self._odin_links(lead, hints)
            if kind != classifier.MEDICAL and "motor" not in links:
                kind = classifier.MEDICAL if kind == classifier.BOTH else classifier.OTHER
        self._odin_send(self._odin_reply_text(kind, hints, links, agent, now))
        self.write({"odin_autoreply_at": now, "odin_autoreply_kind": kind})
        label = dict(self._fields["odin_autoreply_kind"]._description_selection(self.env))[kind]
        lead.message_post(body=_("WhatsApp auto-reply sent (%s).", label),
                          message_type="comment", subtype_xmlid="mail.mt_note")
        if kind == classifier.SERVICE:
            self._odin_escalate(lead, urgent=True)
            self.write({"odin_team_alerted": True, "odin_manager_alerted": True})
        elif kind == "client":
            self._odin_tell(lead, agent, _(
                "Your client %(name)s wrote on WhatsApp and is waiting for you: %(link)s",
                name=self.wa_partner_id.display_name or self.name, link=self._get_html_link()),
                todo=True, summary=_("WhatsApp: reply to %s", self.wa_partner_id.display_name))
            self.odin_team_alerted = True

    def _odin_reply_text(self, kind, hints, links, agent, now):
        """The auto-reply, in plain words, for what the customer wrote."""
        account = self.wa_account_id
        when = account._odin_when_text(now)
        first = self._odin_first_name()
        hi = _("Hi %s", first) if first else _("Hi")
        company = account.company_id.name
        if kind == classifier.SERVICE:
            return _("%(hi)s, thank you. We've received your message and an advisor is attending "
                     "to it. We'll reply here %(when)s.", hi=hi, when=when)
        if kind == "client":
            return _("%(hi)s, thanks for your message. %(agent)s will get back to you here "
                     "%(when)s.", hi=hi, agent=agent.name, when=when)
        if kind == classifier.OTHER:
            return _("%(hi)s, thanks for contacting %(company)s! An advisor will reply here "
                     "%(when)s.", hi=hi, company=company, when=when)
        if kind == classifier.BOTH:
            return _("%(hi)s, thanks for contacting %(company)s! You can get insurance prices in "
                     "about 2 minutes here:\nMedical cover: %(medical)s\nMotor cover: %(motor)s\n"
                     "Or just tell us what you need and an advisor will reply here %(when)s.",
                     hi=hi, company=company, medical=links["medical"], motor=links["motor"],
                     when=when)
        open_now = account._odin_is_open(now)
        lead_in = _("While we get an advisor for you,") if open_now else _(
            "We're offline right now, but")
        if kind == classifier.MEDICAL:
            text = _("%(hi)s, thanks for contacting %(company)s! %(lead_in)s you can see medical "
                     "cover prices in about 2 minutes here: %(link)s",
                     hi=hi, company=company, lead_in=lead_in, link=links["medical"])
        else:
            text = _("%(hi)s, thanks for contacting %(company)s! %(lead_in)s you can compare motor "
                     "insurance prices for your vehicle in about 2 minutes here: %(link)s",
                     hi=hi, company=company, lead_in=lead_in, link=links["motor"])
        if hints.get("question"):
            return "%s %s" % (text, _("An advisor will also answer your questions here %s.", when))
        if not open_now:
            return "%s %s" % (text, _("An advisor will also reply here %s.", when))
        return text

    def _odin_first_name(self):
        name = (self.wa_partner_id.name or "").strip()
        first = name.split(" ")[0] if name else ""
        return "" if not first or first[0] in "+0123456789" else first

    def _odin_send(self, text):
        """Send ``text`` in the conversation as OdooBot, like an agent's message
        but without counting as one."""
        self.ensure_one()
        channel = self.sudo().with_context(odin_wa_auto=True)
        message = channel.with_context(wa_skip_send=True).message_post(
            body=plaintext2html(text), message_type="whatsapp_message",
            subtype_xmlid="mail.mt_comment", author_id=self.env.ref("base.partner_root").id)
        sent = channel._wa_queue_outgoing(message, self.env["ir.attachment"])
        sent.write({"odin_auto": True})
        return sent

    # -- who and where -------------------------------------------------------

    def _odin_owner(self):
        """The person the conversation belongs to, if anyone."""
        self.ensure_one()
        user = self.wa_assigned_user_id or self.wa_lead_id.user_id
        return user if user.active and not user.share else self.env["res.users"]

    def _odin_lead(self):
        """The conversation's lead: linked, the customer's open one, or a new one,
        for the online quotes team while nobody owns it."""
        self.ensure_one()
        lead = self.wa_lead_id
        if not lead or not lead.active or lead.won_status != "pending":
            lead = self._wa_find_lead().sudo()
        if not lead:
            owner = self._odin_owner()
            values = self._wa_lead_values(owner)
            team = self.wa_account_id.company_id.sudo().odin_online_team_id
            if team and not owner:
                values["team_id"] = team.id
            lead = self.env["crm.lead"].with_user(SUPERUSER_ID).with_context(
                mail_create_nosubscribe=True).create(values)
        if self.wa_lead_id != lead:
            self.wa_lead_id = lead
        return lead.sudo()

    def _odin_links(self, lead, hints):
        """The quote pages, signed for this customer's lead (their name and
        phone filled in, the quote on that lead), with what their message
        already says."""
        self.ensure_one()
        models_ = self.env["odin.quote.comparison"]._odin_online_models()
        owner = self._odin_owner()
        base = self.wa_account_id.company_id.get_base_url()
        links = {}
        for line, model in models_.items():
            online = self.env[model].sudo()
            query = {"lead": online.lead_token(lead)}
            if owner:
                query["agent"] = online.agent_token(owner)
            if line == "medical" and hints.get("who"):
                query["who"] = hints["who"]
            if line == "motor":
                vehicle = self.env.ref(hints["vehicle"], raise_if_not_found=False) \
                    if hints.get("vehicle") else False
                if vehicle:
                    query["class_id"] = vehicle.id
                for key in ("cover", "period"):
                    if hints.get(key):
                        query[key] = hints[key]
            links[line] = "%s%s?%s" % (base, online._odin_path(), urlencode(query))
        return links

    def _odin_client_agent(self):
        """The agent of someone who is already a client -- an accepted quote,
        a won opportunity, a confirmed order -- or who is in the middle of a
        quote with an agent. They hear from that agent rather than get a
        link to start again."""
        self.ensure_one()
        partners = self.wa_partner_id
        if self.wa_customer_phone:
            partners |= self.env["res.partner"].sudo().search(
                [("phone_sanitized", "=", self.wa_customer_phone)], limit=20)
        partners = partners.commercial_partner_id
        if not partners:
            return self.env["res.users"]

        def agent(users):
            return users.filtered(lambda u: u.active and not u.share)[:1]

        within = [("partner_id", "child_of", partners.ids)]
        Comparison = self.env["odin.quote.comparison"].sudo()
        accepted = Comparison.search(within + [("state", "=", "accepted")], order="date desc", limit=1)
        won = self.env["crm.lead"].sudo().with_context(active_test=False).search(
            within + [("won_status", "=", "won"), ("user_id", "!=", False)],
            order="date_closed desc", limit=1)
        order = self.env["sale.order"].sudo().search(
            within + [("state", "=", "sale"), ("user_id", "!=", False)], order="date_order desc", limit=1)
        client = agent(accepted.opportunity_id.user_id | accepted.user_id) or agent(won.user_id) \
            or agent(order.user_id)
        if client:
            return client
        quoting = Comparison.search(within + [("state", "in", ("draft", "sent"))],
                                    order="date desc, id desc", limit=5)
        return agent(quoting.opportunity_id.user_id | quoting.user_id)

    def _odin_team(self):
        """Who to tell that a customer is waiting."""
        self.ensure_one()
        owner = self._odin_owner()
        if owner:
            return owner
        users = self.wa_account_id.notify_user_ids
        if not users:
            team = self.wa_account_id.company_id.sudo().odin_online_team_id
            users = team.member_ids | team.user_id
        if not users:
            users = self.env.ref("sales_team.group_sale_manager").sudo().all_user_ids
        return users.filtered(lambda u: u.active and not u.share)

    def _odin_tell(self, lead, users, body, todo=False, summary="", deadline=None):
        """A note on the lead that notifies ``users``, and a to-do for the first."""
        if not users:
            return
        lead.message_post(body=body, partner_ids=users.partner_id.ids,
                          message_type="comment", subtype_xmlid="mail.mt_note")
        if todo:
            lead.with_user(SUPERUSER_ID).activity_schedule(
                "mail.mail_activity_data_todo", user_id=users[0].id,
                summary=summary or _("Reply on WhatsApp"), note=body,
                date_deadline=deadline or fields.Date.context_today(self))

    # -- alerts --------------------------------------------------------------

    def _odin_alerts(self):
        self.ensure_one()
        now = fields.Datetime.now()
        if not self.odin_wait_since or self._odin_is_staff():
            return
        account = self.wa_account_id
        if not self.odin_team_alerted and account._odin_working_seconds(
                self.odin_wait_since, now) >= account.odin_alert_after * 3600:
            self.odin_team_alerted = True
            lead = self._odin_lead()
            self._odin_tell(lead, self._odin_team(), _(
                "%(name)s has been waiting for a reply on WhatsApp since %(since)s: %(link)s",
                name=self.wa_partner_id.display_name or self.name,
                since=self._odin_time_text(self.odin_wait_since), link=self._get_html_link()))
        expires = self.wa_window_expires_at
        if (not self.odin_manager_alerted and expires and expires > now
                and expires - now <= timedelta(hours=account.odin_window_alert)):
            self.odin_manager_alerted = True
            self._odin_escalate(self._odin_lead())

    def _odin_escalate(self, lead, urgent=False):
        """The escalation people get a to-do: at once for service messages,
        else before WhatsApp's window closes."""
        users = self.wa_account_id._odin_escalation_users()
        name = self.wa_partner_id.display_name or self.name
        link = self._get_html_link()
        if urgent:
            body = _("%(name)s sent a payment, a claim or a document on WhatsApp and was told an "
                     "advisor is attending to it: %(link)s", name=name, link=link)
            summary = _("WhatsApp: attend to %s now", name)
        else:
            closes = self._odin_time_text(self.wa_window_expires_at)
            body = _("%(name)s is still waiting for a reply on WhatsApp: %(link)s. Reply before "
                     "%(closes)s; after that WhatsApp only accepts templates, so send Pick up the "
                     "conversation.", name=name, link=link, closes=closes)
            summary = _("WhatsApp: reply to %(name)s before %(closes)s", name=name, closes=closes)
        self._odin_tell(lead, users, body, todo=True, summary=summary)

    def _odin_time_text(self, moment):
        """'9:44pm 24 Oct' or '10am 22 Oct', in the account's time zone."""
        local = self.wa_account_id._odin_local(moment)
        return "%s %s" % (local.strftime("%-I:%M%p").lower().replace(":00", ""),
                          local.strftime("%-d %b"))
