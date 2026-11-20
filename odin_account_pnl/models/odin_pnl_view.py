"""Saved P&L views, optionally emailed on a schedule.

A view keeps everything that shapes the page: the layout, the period *rule*
("last month", "financial year to date"; custom dates stay as typed), the
comparison, filters, display options, and the rows unfolded with their "By"
choice. Opening it, or sending it, computes the period again from today: a
"last month" view emailed on the 2nd of each month always carries the month
just closed.

Sending renders the report **as the view's owner**, in the view's company, so
recipients never receive more than the owner may see. Mail goes through the
standard queue (``mail.mail``), processed by Odoo's mail cron.
https://www.odoo.com/documentation/19.0/developer/reference/backend/actions.html#automated-actions-ir-cron

A schedule runs every day, every weekday, every week, every month or every
quarter, at an hour of the owner's time zone. It can be paused, tested on
oneself first, and keeps a history of what was sent; three scheduled sends
failing in a row pause it, rather than fail silently every period.
"""

import base64
import logging
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

import pytz
from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_normalize, email_split, format_datetime, formataddr

from . import odin_pnl_periods as periods

_logger = logging.getLogger(__name__)


def _deliver(mail):
    """Send ``mail`` now and return ``(state, failure reason)``.

    Sent at once, not left for the hourly mail queue. auto_delete is off while
    sending so the outcome can be read: a mail that went is then removed, as
    auto_delete would have; one the server refused stays (state 'exception')
    for a retry from the mail queue, and goes once that sends it. 'outgoing'
    means nothing was sent here (sending is off under tests); the queue sends it.
    """
    mail.send(raise_exception=False)
    state, reason = mail.state, _server_reply(mail.failure_reason or "")
    if state == "sent":
        mail.unlink()
    elif state == "exception":
        mail.auto_delete = True
    return state, reason


def _server_reply(reason):
    """The last line of a failure: the relay's own words. Odoo puts two lines
    of its own first ("Mail Delivery Failed", "Mail delivery failed via SMTP
    server 'Resend'."), and those used up the status before the part that
    says why."""
    lines = [line.strip() for line in reason.splitlines() if line.strip()]
    return lines[-1] if lines else ""


SCHEDULES = [
    ("none", "Off"),
    ("daily", "Every day"),
    ("weekdays", "Every weekday"),
    ("weekly", "Every week"),
    ("monthly", "Every month"),
    ("quarterly", "Every quarter"),
]
WEEKDAYS = [
    ("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"), ("3", "Thursday"),
    ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday"),
]
# The sending hour, as a time to pick rather than a number to type. Stored as
# its hour ("7"), which is what the integer column it replaces converts to.
HOURS = [(str(hour), f"{hour:02d}:00") for hour in range(24)]
DEFAULT_HOUR = 7
# A quarterly schedule sends in the month after each calendar quarter, as the
# "Last quarter" period counts them.
QUARTER_MONTHS = (1, 4, 7, 10)
# Scheduled sends failing this many times in a row pause the schedule.
MAX_FAILURES = 3
# Sends remembered per view.
HISTORY_SIZE = 30
# Options that only make sense for the click that produced them.
TRANSIENT_OPTIONS = ("step",)
# Email colours: neutral greys, and a green and a red that read on white.
GOOD = ("#047857", "#ecfdf5")
BAD = ("#b91c1c", "#fef2f2")
MUTED = "#6b7280"
INK = "#111827"
RULE = "#e5e7eb"


class OdinPnlView(models.Model):
    _name = "odin.pnl.view"
    _description = "Saved P&L view"
    _order = "name, id"

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users", string="Owner", required=True, default=lambda self: self.env.user, index=True,
        ondelete="cascade")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    # A view of companies side by side keeps them: it is emailed with all of
    # them, not with its own company alone.
    company_ids = fields.Many2many(
        "res.company", string="Companies",
        help="The companies a view of companies side by side shows. Empty: the view's company.")
    shared = fields.Boolean(help="Everyone who can see the P&L finds this view in their list.")
    is_default = fields.Boolean(string="My default", help="Opened when the owner opens the P&L.")
    # lambda, not `dict`: Odoo calls a callable default as field.default(self),
    # so `default=dict` evaluates dict(recordset) -- a recordset iterates as
    # records, and dict() wants pairs, so opening the form raised "dictionary
    # update sequence element #0 has length 1; 2 is required".
    #
    # NOT `required=True`. fields.Json.convert_to_cache maps every falsy value
    # to None, so an empty dict is stored as SQL NULL -- a required Json column
    # can therefore never be satisfied by this field's own default, and the
    # form (which has no options field, because options come from the report)
    # could only ever create `{}`. Saving a view from the Saved P&L Views menu
    # and pressing "Send now" raised "Missing required value for the field
    # 'Options'", Odoo's wording for the NOT NULL violation. Nothing needs the
    # constraint: convert_to_record turns NULL back into {}, every reader here
    # goes through `self.options or {}`, and _normalize_options fills in each
    # default, so a view with no stored options is simply the default P&L.
    options = fields.Json(default=lambda self: {})
    period_rule = fields.Char(string="Period", compute="_compute_period_rule")
    period_is_fixed = fields.Boolean(
        compute="_compute_period_rule",
        help="The view shows dates typed by hand, so every email carries the same dates.")

    schedule = fields.Selection(SCHEDULES, string="Send", required=True, default="none")
    schedule_paused = fields.Boolean(string="Paused", help="Kept as set up, but not sent until resumed.")
    send_weekday = fields.Selection(WEEKDAYS, string="On", default="0")
    send_day = fields.Integer(string="Day of the month", default=2, help="1 to 28.")
    send_hour = fields.Selection(
        HOURS, string="At", required=True, default=str(DEFAULT_HOUR),
        help="In the owner's time zone.")
    schedule_summary = fields.Char(compute="_compute_schedule_summary")
    recipient_ids = fields.Many2many("res.partner", string="Recipients")
    recipient_emails = fields.Char(string="Other emails", help="Comma-separated addresses.")
    subject = fields.Char(
        help="Leave empty for the report's name and period, as “Profit & Loss · Oct 2026”. "
             "{title}, {period} and {company} are replaced.")
    message = fields.Text(help="A short note above the figures in the email.")
    send_pdf = fields.Boolean(string="PDF", default=True)
    send_xlsx = fields.Boolean(string="Excel")
    include_summary = fields.Boolean(
        string="Key figures in the email", default=True,
        help="The headline figures and the lines of the P&L in the email itself, readable on a phone. "
             "Untick to keep the figures in the attachments only.")
    next_send = fields.Datetime(compute="_compute_next_send", store=True, readonly=True)
    last_sent = fields.Datetime(readonly=True)
    last_status = fields.Char(readonly=True)
    last_failed = fields.Boolean(readonly=True)
    failure_count = fields.Integer(readonly=True, help="Sends failed in a row.")
    log_ids = fields.One2many("odin.pnl.view.log", "view_id", string="History", readonly=True)

    @api.depends("options")
    def _compute_period_rule(self):
        labels = {
            "this_month": _("This month"),
            "previous_month": _("Last month"),
            "this_quarter": _("This quarter"),
            "previous_quarter": _("Last quarter"),
            "this_year": _("This financial year"),
            "previous_year": _("Last financial year"),
            "year_to_date": _("Financial year to date"),
        }
        for view in self:
            date_opt = (view.options or {}).get("date") or {}
            # No stored options at all (a view made from the menu) means the
            # default P&L, which is what _normalize_options will render --
            # without this the label read "None to None".
            preset = date_opt.get("filter") or (periods.DEFAULT_PRESET if not date_opt else None)
            view.period_rule = labels.get(preset) or _(
                "%(start)s to %(end)s", start=date_opt.get("date_from"), end=date_opt.get("date_to"))
            view.period_is_fixed = preset not in labels

    @api.depends("schedule", "schedule_paused", "send_weekday", "send_day", "send_hour", "user_id.tz", "last_sent")
    def _compute_next_send(self):
        now = fields.Datetime.now()
        for view in self:
            active = view.schedule != "none" and not view.schedule_paused
            view.next_send = view._next_send_after(now) if active else False

    @api.depends("schedule", "schedule_paused", "send_weekday", "send_day", "send_hour", "user_id.tz")
    def _compute_schedule_summary(self):
        weekdays = dict(self._fields["send_weekday"]._description_selection(self.env))
        for view in self:
            if view.schedule == "none":
                view.schedule_summary = _("Not emailed")
                continue
            time = f"{view._hour():02d}:00"
            day = min(max(view.send_day or 1, 1), 28)
            summary = {
                "daily": _("Every day at %(time)s", time=time),
                "weekdays": _("Every weekday, Monday to Friday, at %(time)s", time=time),
                "weekly": _("Every %(weekday)s at %(time)s",
                            weekday=weekdays.get(view.send_weekday or "0", ""), time=time),
                "monthly": _("Every month on day %(day)s at %(time)s", day=day, time=time),
                "quarterly": _("Every quarter on day %(day)s of January, April, July and October at %(time)s",
                               day=day, time=time),
            }[view.schedule]
            summary = f"{summary} ({view.user_id.tz or 'UTC'})"
            view.schedule_summary = _("Paused: %(summary)s", summary=summary) if view.schedule_paused else summary

    @api.constrains("send_day", "schedule", "recipient_ids", "recipient_emails", "send_pdf", "send_xlsx",
                    "include_summary")
    def _check_schedule(self):
        for view in self.filtered(lambda view: view.schedule != "none"):
            if view.schedule in ("monthly", "quarterly") and not 1 <= view.send_day <= 28:
                raise ValidationError(_("Pick a day of the month from 1 to 28."))
            if not view._recipient_emails():
                raise ValidationError(_("Add at least one recipient with an email address."))
            if not (view.send_pdf or view.send_xlsx or view.include_summary):
                raise ValidationError(_("Attach the PDF or the Excel file, or put the key figures in the email."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("schedule", "none") != "none":
                self._check_can_schedule()
            if (vals.get("options") or {}).get("by_company") and "company_ids" not in vals:
                # Scheduled from a page of companies side by side.
                vals["company_ids"] = [(6, 0, self.env.companies.ids)]
        views = super().create(vals_list)
        views.filtered("is_default")._unset_other_defaults()
        return views

    def write(self, vals):
        if vals.get("schedule", "none") != "none" or {"recipient_ids", "recipient_emails"} & set(vals):
            self._check_can_schedule()
        if vals.get("schedule_paused") is False:
            # Resumed: a fresh start, not one failure away from pausing again.
            vals = dict(vals, failure_count=0)
        result = super().write(vals)
        if vals.get("is_default"):
            self._unset_other_defaults()
        return result

    def _check_can_schedule(self):
        # Emailing ledger figures out of Odoo is an accountant's decision.
        if not self.env.su and not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can email the P&L on a schedule."))

    def _check_owner(self):
        """Sending, testing and pausing are the owner's (or an administrator's)."""
        for view in self:
            if view.user_id != self.env.user and not self.env.user.has_group("account.group_account_manager"):
                raise AccessError(_("Only the owner of “%(view)s” can do this.", view=view.name))

    def _unset_other_defaults(self):
        for view in self:
            self.search([
                ("id", "!=", view.id), ("user_id", "=", view.user_id.id), ("is_default", "=", True),
            ]).write({"is_default": False})

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _hour(self):
        try:
            return min(max(int(self.send_hour), 0), 23)
        except (TypeError, ValueError):
            return DEFAULT_HOUR

    def _next_send_after(self, moment):
        """The next sending time after ``moment`` (naive UTC), at the chosen
        hour in the owner's time zone."""
        self.ensure_one()
        tz = pytz.timezone(self.user_id.tz or "UTC")
        local = pytz.utc.localize(moment).astimezone(tz)
        hour = self._hour()
        today = local.date()

        def at(day):
            return tz.localize(datetime(day.year, day.month, day.day, hour))

        if self.schedule in ("daily", "weekdays"):
            day = today
            while at(day) <= local or (self.schedule == "weekdays" and day.weekday() >= 5):
                day += timedelta(days=1)
        elif self.schedule == "weekly":
            day = today + timedelta(days=(int(self.send_weekday or 0) - today.weekday()) % 7)
            if at(day) <= local:
                day += timedelta(days=7)
        elif self.schedule == "quarterly":
            month_day = min(max(self.send_day, 1), 28)
            day = date(today.year, QUARTER_MONTHS[(today.month - 1) // 3], month_day)
            while at(day) <= local:
                day += relativedelta(months=3)
        else:
            day = today.replace(day=min(max(self.send_day, 1), 28))
            if at(day) <= local:
                day += relativedelta(months=1)
        return at(day).astimezone(pytz.utc).replace(tzinfo=None)

    def _recipient_emails(self):
        """Each address once. A recipient picked as a contact and also typed
        under "Other emails" was listed twice, and counted twice in "Sent to"."""
        emails = [partner.email_formatted for partner in self.recipient_ids if partner.email]
        emails += email_split(self.recipient_emails or "")
        unique, seen = [], set()
        for email in emails:
            key = email_normalize(email) or email
            if key not in seen:
                seen.add(key)
                unique.append(email)
        return unique

    @api.model
    def _cron_send_due(self):
        """Send the views whose time has come, one by one, committing after
        each so a failure does not resend the others."""
        # The cron's user (OdooBot) lives in one company; the views are in
        # every company. They are found without record rules here, and each
        # one is rendered as its owner in ``_send``.
        due = self.sudo().search([
            ("schedule", "!=", "none"), ("schedule_paused", "=", False),
            ("next_send", "<=", fields.Datetime.now()),
        ], limit=50)
        cron = self.env["ir.cron"]
        cron._commit_progress(remaining=len(due))
        for view in due:
            view._send_and_record()
            cron._commit_progress(1)

    def _send_and_record(self):
        return self._send_and_record_result()[0]

    def _send_and_record_result(self, kind="scheduled", emails=None):
        """Send now and write the outcome on the view and in its history:
        ``(status, failed)``. ``kind``: ``scheduled`` (the cron), ``manual``
        (Send now) or ``test`` (to ``emails`` only, the schedule untouched)."""
        self.ensure_one()
        failed = False
        emails = emails or self._recipient_emails()
        try:
            with self.env.cr.savepoint():
                state, reason = self._send(emails, test=kind == "test")
            if state == "exception":
                failed = True
                # The address goes in the status: which sender a relay refused
                # is most of the diagnosis.
                sender = email_normalize(self._sender(self.user_id)) or _("no address")
                _logger.warning("odin_account_pnl: view %s, from %s, was refused by the mail server: %s",
                                self.id, sender, reason)
                status = _("Not sent from %(sender)s: %(error)s", sender=sender,
                           error=(reason or _("the mail server refused it"))[:300])
            elif state == "outgoing":
                status = _("Queued for %(count)s recipient(s)", count=len(emails))
            else:
                status = _("Sent to %(count)s recipient(s)", count=len(emails))
        except Exception as error:  # noqa: BLE001 (record any failure on the view, keep going)
            _logger.warning("odin_account_pnl: view %s could not be sent: %s", self.id, error)
            failed = True
            status = _("Not sent: %(error)s", error=str(error)[:200])
        if kind != "test":
            values = {
                "last_sent": fields.Datetime.now(),
                "last_failed": failed,
                "failure_count": self.failure_count + 1 if failed else 0,
            }
            if failed and kind == "scheduled" and values["failure_count"] >= MAX_FAILURES:
                values["schedule_paused"] = True
                status = _("%(status)s Paused after %(count)s failed sends in a row: fix it, then resume.",
                           status=status, count=values["failure_count"])
            values["last_status"] = status
            self.sudo().write(values)
        Log = self.env["odin.pnl.view.log"].sudo()
        Log.create({
            "view_id": self.id,
            "kind": kind,
            "status": status,
            "failed": failed,
            "recipients": ", ".join(emails)[:1000],
        })
        Log.search([("view_id", "=", self.id)], offset=HISTORY_SIZE).unlink()
        return status, failed

    def _subject(self, payload):
        template = (self.subject or "").strip()
        if not template:
            return f"{payload['title']} · {payload['period']}"
        # Plain replacements, never str.format: a subject is typed text.
        for token, value in (("{title}", payload["title"]), ("{period}", payload["period"]),
                             ("{company}", payload["company"])):
            template = template.replace(token, value or "")
        return template

    def _send(self, emails=None, test=False):
        """Render as the owner, in the view's company, and send the email:
        ``(state, failure reason)`` of the mail, see ``_deliver``."""
        self.ensure_one()
        owner = self.user_id
        if not owner.active or not owner.has_group("account.group_account_readonly"):
            raise UserError(_("%(owner)s can no longer see the P&L.", owner=owner.name))
        emails = emails or self._recipient_emails()
        if not emails:
            raise UserError(_("Add at least one recipient with an email address."))
        companies = [self.company_id.id] + [cid for cid in self.company_ids.ids if cid != self.company_id.id]
        env = self.with_user(owner).with_context(
            allowed_company_ids=companies, lang=owner.lang, tz=owner.tz).env
        options = self._live_options()
        Export = env["odin.pnl.export"]
        payload = Export._payload(options)
        attachments = env["ir.attachment"]
        files = []
        if self.send_pdf:
            files.append(Export.render(options, "pdf"))
        if self.send_xlsx:
            files.append(Export.render(options, "xlsx"))
        for content, filename, mimetype in files:
            attachments |= self.env["ir.attachment"].sudo().create({
                "name": filename,
                "datas": base64.b64encode(content),
                "mimetype": mimetype,
                "res_model": self._name,
                "res_id": self.id,
            })
        sender = self._sender(owner)
        headers = {}
        if not self.company_id.bounce_email and email_normalize(sender):
            # The envelope sender follows the From. Without a bounce address
            # Odoo falls back to the server's email_from, which on an Odin box
            # is the instance's own host -- the domain the relay refuses.
            headers["Return-Path"] = email_normalize(sender)
        # The server Odoo would pick, named on the mail. Left to choose, Odoo
        # also puts its own identity back in the From whenever the relay's
        # from_filter does not name this address -- the host-name address the
        # relay refuses.
        relay = self.env["ir.mail_server"].sudo()._find_mail_server(sender)[0]
        subject = self._subject(payload)
        if test:
            subject = _("[Test] %(subject)s", subject=subject)
        mail = self.env["mail.mail"].sudo().create({
            "subject": subject,
            # NOT the owner's own address. An ODIN instance sends through one
            # verified identity (ir.mail_server.from_filter), so a personal
            # address is refused outright -- Resend answered 550 "This API key
            # is not authorized to send emails from gmail.com", and an owner
            # with no email at all produced mail_from_missing. Odoo's own
            # default identity is the address the relay accepts; the owner
            # belongs in Reply-To, which is what a recipient actually needs.
            "email_from": sender,
            "headers": repr(headers) if headers else False,
            "mail_server_id": relay.id if relay else False,
            "reply_to": owner.email_formatted or self.company_id.email_formatted,
            # The owner, not the cron's user: Odoo names the Reply-To after the author.
            "author_id": owner.partner_id.id,
            "email_to": ", ".join(emails),
            "body_html": self._email_body(payload, attached=[filename for _content, filename, _type in files]),
            "attachment_ids": [(6, 0, attachments.ids)],
            "auto_delete": False,
        })
        return _deliver(mail)

    def _sender(self, owner):
        """``"Company" <noreply@odin.ist>``: who the email is from.

        A company with an alias domain sends as its default sender, as every
        other app does (noreply@odin.ist on BizSure). Without one, the identity
        is the server's own: its ``email_from``, else what the relay's
        ``from_filter`` names -- one address, or a domain to send from as
        noreply@. Only with no identity at all does the company's or the
        owner's own address stand in.

        The server's identity sits on the instance's own host name,
        noreply@elora.odin.ist, and Resend refuses it with 403 "not authorized
        to send emails from elora.odin.ist": the relay's key is for odin.ist,
        the domain Helpdesk and the other apps send from. So an address on
        this instance's host name moves to the parent domain.
        """
        company = self.company_id
        address = company.default_from_email
        if not address:
            Server = self.env["ir.mail_server"].sudo().with_company(company)
            relay = Server.search([], order="sequence, id", limit=1).from_filter or Server._get_default_from_filter()
            parts = Server._parse_from_filter(relay)
            named = [part for part in parts if "@" in part]
            domains = [part for part in parts if "@" not in part]
            address = email_normalize(Server._get_default_from_address() or (named[0] if len(named) == 1 else ""))
            if address:
                local, domain = address.rsplit("@", 1)
                host = urlsplit(self.get_base_url()).hostname or ""
                if domain == host and host.count(".") >= 2:
                    address = f"{local}@{host.split('.', 1)[1]}"
            elif domains:
                address = f"noreply@{domains[0]}"
        if not address:
            return company.email_formatted or owner.email_formatted
        return formataddr((company.name, address))

    def _live_options(self):
        options = dict(self.options or {})
        for key in TRANSIENT_OPTIONS:
            options.pop(key, None)
        if isinstance(options.get("date"), dict):
            options["date"] = {k: v for k, v in options["date"].items() if k != "step"}
        return options

    # ------------------------------------------------------------------
    # The email
    # ------------------------------------------------------------------

    def _report_url(self):
        """The P&L opened on this view (``pnl_view``, read by the page)."""
        return f"{self.get_base_url()}/odoo/pnl?pnl_view={self.id}"

    def _email_body(self, payload, attached=()):
        """An email laid out for every mail client (tables, inline styles):
        the headline figures as tiles, then the lines of the P&L with the
        change against the comparison, and a button to the report. The
        figures read on a phone without opening the attachment."""
        message = escape(self.message or "").replace("\n", Markup("<br/>"))
        columns = payload["columns"]
        balance = [index for index, column in enumerate(columns) if column["kind"] == "balance"]
        current = next((index for index in balance if columns[index].get("group") == "total"),
                       balance[0] if balance else None)
        previous = next((index for index in balance if columns[index].get("group") == "comparison"), None)
        variance = next((index for index, column in enumerate(columns) if column["kind"] == "variance"), None)
        growth = next((index for index, column in enumerate(columns) if column["kind"] == "growth"), None)
        comparison_label = columns[previous]["label"] if previous is not None else ""
        figures = Markup("")
        if self.include_summary:
            figures = self._email_kpis(payload["kpis"], comparison_label) + self._email_lines(
                payload, current, previous, variance, growth)
        button = (self.company_id.primary_color or "").strip() or INK
        notes = Markup("")
        if attached:
            notes = Markup('<p style="margin:0 0 6px;color:{muted};font-size:12px">{text}</p>').format(
                muted=MUTED, text=_("Attached: %(files)s", files=", ".join(attached)))
        return Markup(
            '<div style="margin:0;padding:24px 12px;background:#f3f4f6;'
            'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
            'color:{ink};font-size:14px;line-height:1.45">'
            '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
            'style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid {rule};border-radius:12px">'
            '<tr><td style="padding:24px 28px 4px">'
            '<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:{muted}">{company}</div>'
            '<div style="font-size:22px;font-weight:700;margin-top:4px">{title}</div>'
            '<div style="color:{muted};margin-top:2px">{period}{versus}</div>'
            "</td></tr>"
            '<tr><td style="padding:12px 28px 0">{message}</td></tr>'
            "{figures}"
            '<tr><td style="padding:20px 28px 8px">'
            '<a href="{url}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:{button};'
            'color:#ffffff;text-decoration:none;font-weight:600">{open}</a>'
            "</td></tr>"
            '<tr><td style="padding:8px 28px 24px">{notes}'
            '<p style="margin:0;color:{muted};font-size:12px">{footer}</p>'
            "</td></tr>"
            "</table></div>"
        ).format(
            ink=INK, rule=RULE, muted=MUTED, button=button,
            company=payload["company"],
            title=payload["title"],
            period=payload["period"],
            versus=_(" · compared with %(period)s", period=comparison_label) if comparison_label else "",
            message=Markup('<p style="margin:0 0 4px">{}</p>').format(message) if self.message else "",
            figures=figures,
            url=self._report_url(),
            open=_("Open the P&L in Odoo"),
            notes=notes,
            footer=_("Sent on behalf of %(owner)s: %(schedule)s.",
                     owner=self.user_id.name, schedule=self.schedule_summary)
            if self.schedule != "none" else _("Sent on behalf of %(owner)s.", owner=self.user_id.name),
        )

    def _pill(self, text, good):
        if not text:
            return Markup("")
        if good is None:
            color, background = MUTED, "#f3f4f6"
        else:
            color, background = GOOD if good else BAD
        return Markup(
            '<span style="display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;'
            'font-weight:600;color:{color};background:{background};white-space:nowrap">{text}</span>'
        ).format(color=color, background=background, text=text)

    def _email_kpis(self, kpis, comparison_label):
        if not kpis:
            return Markup("")
        width = 100 // len(kpis)
        cells = []
        for kpi in kpis:
            change = Markup("")
            if kpi.get("growth_text") or kpi.get("variance_text"):
                change = Markup('<div style="margin-top:6px">{pill} <span style="color:{muted};font-size:12px">'
                                "{versus}</span></div>").format(
                    pill=self._pill(kpi.get("growth_text") or kpi.get("variance_text"), kpi.get("growth_good")),
                    muted=MUTED, versus=_("vs %(period)s", period=comparison_label))
            margin = Markup("")
            if kpi.get("margin_text"):
                margin = Markup('<div style="color:{muted};font-size:12px;margin-top:4px">{text}</div>').format(
                    muted=MUTED, text=_("Margin %(margin)s", margin=kpi["margin_text"]))
            cells.append(Markup(
                '<td width="{width}%" valign="top" style="padding:6px">'
                '<div style="border:1px solid {rule};border-radius:10px;padding:12px 14px">'
                '<div style="color:{muted};font-size:12px">{name}</div>'
                '<div style="font-size:18px;font-weight:700;margin-top:2px;white-space:nowrap">{value}</div>'
                "{change}{margin}</div></td>"
            ).format(width=width, rule=RULE, muted=MUTED, name=kpi["name"], value=kpi.get("text") or "-",
                     change=change, margin=margin))
        return Markup(
            '<tr><td style="padding:12px 22px 0"><table role="presentation" cellpadding="0" cellspacing="0" '
            'width="100%"><tr>{cells}</tr></table></td></tr>').format(cells=Markup("").join(cells))

    def _email_lines(self, payload, current, previous, variance, growth):
        rows = [row for row in payload["rows"] if row["type"] in ("line", "formula") and not row.get("parent")]
        if not rows or current is None:
            return Markup("")
        columns = payload["columns"]
        cell = 'padding:7px 8px;border-bottom:1px solid {rule};text-align:right;white-space:nowrap'.format(rule=RULE)
        head = Markup('<th style="{style};font-size:12px;color:{muted};font-weight:600">{label}</th>')
        header = [Markup('<th style="padding:7px 8px;border-bottom:1px solid {rule};text-align:left;font-size:12px;'
                         'color:{muted};font-weight:600">{label}</th>').format(
            rule=RULE, muted=MUTED, label=payload["currency"])]
        header.append(head.format(style=cell, muted=MUTED, label=columns[current]["label"]))
        if previous is not None:
            header.append(head.format(style=cell, muted=MUTED, label=columns[previous]["label"]))
        if growth is not None:
            header.append(head.format(style=cell, muted=MUTED, label=_("Change")))
        lines = []
        for row in rows:
            weight = "700" if row.get("bold") else "400"
            top = f"border-top:2px solid {INK};" if row.get("is_result") else ""
            cells = [Markup('<td style="padding:7px 8px;border-bottom:1px solid {rule};{top}font-weight:{w}">'
                            "{name}</td>").format(rule=RULE, top=top, w=weight, name=row["name"])]
            for index in (current, previous):
                if index is None:
                    continue
                cells.append(Markup('<td style="{style};{top}font-weight:{w}">{value}</td>').format(
                    style=cell, top=top, w=weight, value=row["cells"][index]["text"]))
            if growth is not None:
                text = row["cells"][growth]["text"] or (row["cells"][variance]["text"] if variance is not None else "")
                cells.append(Markup('<td style="{style};{top}">{pill}</td>').format(
                    style=cell, top=top, pill=self._pill(text, row.get("growth_good"))))
            lines.append(Markup("<tr>{}</tr>").format(Markup("").join(cells)))
        return Markup(
            '<tr><td style="padding:16px 28px 0"><table role="presentation" cellpadding="0" cellspacing="0" '
            'width="100%" style="border-collapse:collapse;font-size:13px"><thead><tr>{header}</tr></thead>'
            "<tbody>{lines}</tbody></table></td></tr>"
        ).format(header=Markup("").join(header), lines=Markup("").join(lines))

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _notify(self, message, failed=False):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": message, "type": "warning" if failed else "success", "sticky": failed},
        }

    def action_send_now(self):
        self.ensure_one()
        self._check_owner()
        self._check_can_schedule()
        if not self._recipient_emails():
            raise UserError(_("Add at least one recipient with an email address."))
        status, failed = self._send_and_record_result("manual")
        return self._notify(status, failed)

    def action_send_test(self):
        """The email as the recipients will get it, to oneself only."""
        self.ensure_one()
        self._check_owner()
        self._check_can_schedule()
        email = self.env.user.email_formatted if self.env.user.email else False
        if not email:
            raise UserError(_("Your user has no email address. Add one in your preferences to receive the test."))
        status, failed = self._send_and_record_result("test", [email])
        return self._notify(_("Test: %(status)s", status=status), failed)

    def action_toggle_pause(self):
        self.ensure_one()
        self._check_owner()
        paused = not self.schedule_paused
        self.write({"schedule_paused": paused})
        return self._notify(
            _("“%(view)s” is paused.", view=self.name) if paused
            else _("“%(view)s” is resumed: next on %(when)s.", view=self.name,
                   when=format_datetime(self.env, self.next_send, dt_format="medium") if self.next_send else "-"))

    def action_open_report(self):
        """The P&L opened on this view."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "odin_account_pnl.report",
            "name": _("Profit & Loss"),
            "params": {"view_id": self.id},
        }


class OdinPnlViewLog(models.Model):
    """What a saved view sent, and how it went: the history of its emails."""

    _name = "odin.pnl.view.log"
    _description = "Saved P&L view: email sent"
    _order = "id desc"

    view_id = fields.Many2one("odin.pnl.view", required=True, ondelete="cascade", index=True)
    kind = fields.Selection(
        [("scheduled", "Scheduled"), ("manual", "Sent now"), ("test", "Test")], required=True)
    status = fields.Char()
    failed = fields.Boolean()
    recipients = fields.Char()
    user_id = fields.Many2one("res.users", string="By", default=lambda self: self.env.user)
