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
"""

import base64
import logging
from datetime import datetime, timedelta

import pytz
from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import email_split

_logger = logging.getLogger(__name__)

SCHEDULES = [
    ("none", "Not emailed"),
    ("daily", "Every day"),
    ("weekly", "Every week"),
    ("monthly", "Every month"),
]
WEEKDAYS = [
    ("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"), ("3", "Thursday"),
    ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday"),
]
# Options that only make sense for the click that produced them.
TRANSIENT_OPTIONS = ("step",)


class OdinPnlView(models.Model):
    _name = "odin.pnl.view"
    _description = "Saved P&L view"
    _order = "name, id"

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        "res.users", string="Owner", required=True, default=lambda self: self.env.user, index=True,
        ondelete="cascade")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    shared = fields.Boolean(help="Everyone who can see the P&L finds this view in their list.")
    is_default = fields.Boolean(string="My default", help="Opened when the owner opens the P&L.")
    # lambda, not `dict`: Odoo calls a callable default as field.default(self),
    # so `default=dict` evaluates dict(recordset) -- a recordset iterates as
    # records, and dict() wants pairs, so opening the form raised "dictionary
    # update sequence element #0 has length 1; 2 is required".
    options = fields.Json(required=True, default=lambda self: {})
    period_rule = fields.Char(compute="_compute_period_rule")

    schedule = fields.Selection(SCHEDULES, required=True, default="none")
    send_weekday = fields.Selection(WEEKDAYS, string="On", default="0")
    send_day = fields.Integer(string="Day of the month", default=2, help="1 to 28.")
    send_hour = fields.Integer(string="At (hour)", default=7, help="In the owner's time zone, 0 to 23.")
    recipient_ids = fields.Many2many("res.partner", string="Recipients")
    recipient_emails = fields.Char(string="Other emails", help="Comma-separated addresses.")
    send_pdf = fields.Boolean(string="PDF", default=True)
    send_xlsx = fields.Boolean(string="Excel")
    message = fields.Text(help="A short note above the figures in the email.")
    next_send = fields.Datetime(compute="_compute_next_send", store=True, readonly=True)
    last_sent = fields.Datetime(readonly=True)
    last_status = fields.Char(readonly=True)

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
            preset = date_opt.get("filter")
            view.period_rule = labels.get(preset) or _(
                "%(start)s to %(end)s", start=date_opt.get("date_from"), end=date_opt.get("date_to"))

    @api.depends("schedule", "send_weekday", "send_day", "send_hour", "user_id.tz", "last_sent")
    def _compute_next_send(self):
        now = fields.Datetime.now()
        for view in self:
            view.next_send = view._next_send_after(now) if view.schedule != "none" else False

    @api.constrains("send_day", "send_hour", "schedule", "recipient_ids", "recipient_emails", "send_pdf", "send_xlsx")
    def _check_schedule(self):
        for view in self.filtered(lambda view: view.schedule != "none"):
            if not 1 <= view.send_day <= 28:
                raise ValidationError(_("Pick a day of the month from 1 to 28."))
            if not 0 <= view.send_hour <= 23:
                raise ValidationError(_("Pick an hour from 0 to 23."))
            if not view._recipient_emails():
                raise ValidationError(_("Add at least one recipient with an email address."))
            if not (view.send_pdf or view.send_xlsx):
                raise ValidationError(_("Attach the PDF, the Excel file or both."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("schedule", "none") != "none":
                self._check_can_schedule()
        views = super().create(vals_list)
        views.filtered("is_default")._unset_other_defaults()
        return views

    def write(self, vals):
        if vals.get("schedule", "none") != "none" or {"recipient_ids", "recipient_emails"} & set(vals):
            self._check_can_schedule()
        result = super().write(vals)
        if vals.get("is_default"):
            self._unset_other_defaults()
        return result

    def _check_can_schedule(self):
        # Emailing ledger figures out of Odoo is an accountant's decision.
        if not self.env.su and not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can email the P&L on a schedule."))

    def _unset_other_defaults(self):
        for view in self:
            self.search([
                ("id", "!=", view.id), ("user_id", "=", view.user_id.id), ("is_default", "=", True),
            ]).write({"is_default": False})

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _next_send_after(self, moment):
        """The next sending time after ``moment`` (naive UTC), at the chosen
        hour in the owner's time zone."""
        self.ensure_one()
        tz = pytz.timezone(self.user_id.tz or "UTC")
        local = pytz.utc.localize(moment).astimezone(tz)
        hour = min(max(self.send_hour, 0), 23)

        def at(day):
            return tz.localize(datetime(day.year, day.month, day.day, hour))

        if self.schedule == "daily":
            candidate = at(local.date())
            if candidate <= local:
                candidate = at(local.date() + timedelta(days=1))
        elif self.schedule == "weekly":
            ahead = (int(self.send_weekday or 0) - local.weekday()) % 7
            candidate = at(local.date() + timedelta(days=ahead))
            if candidate <= local:
                candidate = at(local.date() + timedelta(days=ahead + 7))
        else:
            day = min(max(self.send_day, 1), 28)
            candidate = at(local.date().replace(day=day))
            if candidate <= local:
                candidate = at(local.date().replace(day=day) + relativedelta(months=1))
        return candidate.astimezone(pytz.utc).replace(tzinfo=None)

    def _recipient_emails(self):
        emails = [partner.email_formatted for partner in self.recipient_ids if partner.email]
        emails += email_split(self.recipient_emails or "")
        return emails

    @api.model
    def _cron_send_due(self):
        """Send the views whose time has come, one by one, committing after
        each so a failure does not resend the others."""
        # The cron's user (OdooBot) lives in one company; the views are in
        # every company. They are found without record rules here, and each
        # one is rendered as its owner in ``_send``.
        due = self.sudo().search(
            [("schedule", "!=", "none"), ("next_send", "<=", fields.Datetime.now())], limit=50)
        cron = self.env["ir.cron"]
        cron._commit_progress(remaining=len(due))
        for view in due:
            view._send_and_record()
            cron._commit_progress(1)

    def _send_and_record(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self._send()
            status = _("Sent to %(count)s recipient(s)", count=len(self._recipient_emails()))
        except Exception as error:  # noqa: BLE001 (record any failure on the view, keep going)
            _logger.warning("odin_account_pnl: view %s could not be sent: %s", self.id, error)
            status = _("Not sent: %(error)s", error=str(error)[:200])
        self.sudo().write({"last_sent": fields.Datetime.now(), "last_status": status})
        return status

    def _send(self):
        """Render as the owner, in the view's company, and queue the email."""
        self.ensure_one()
        owner = self.user_id
        if not owner.active or not owner.has_group("account.group_account_readonly"):
            raise UserError(_("%(owner)s can no longer see the P&L.", owner=owner.name))
        env = self.with_user(owner).with_context(
            allowed_company_ids=[self.company_id.id], lang=owner.lang, tz=owner.tz).env
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
        self.env["mail.mail"].sudo().create({
            "subject": f"{payload['title']} · {payload['period']}",
            "email_from": owner.email_formatted or self.company_id.email_formatted,
            "email_to": ", ".join(self._recipient_emails()),
            "body_html": self._email_body(payload),
            "attachment_ids": [(6, 0, attachments.ids)],
            "auto_delete": True,
        })

    def _live_options(self):
        options = dict(self.options or {})
        for key in TRANSIENT_OPTIONS:
            options.pop(key, None)
        if isinstance(options.get("date"), dict):
            options["date"] = {k: v for k, v in options["date"].items() if k != "step"}
        return options

    def _email_body(self, payload):
        """The key lines in the email itself, so the figures read on a phone
        without opening the attachment."""
        rows = [row for row in payload["rows"] if row["type"] in ("line", "formula") and not row.get("parent")]
        balance_columns = [index for index, column in enumerate(payload["columns"]) if column["kind"] == "balance"]
        growth_column = next(
            (index for index, column in enumerate(payload["columns"]) if column["kind"] == "growth"), None)
        lines = []
        for row in rows:
            first = row["cells"][balance_columns[0]]["text"] if balance_columns else ""
            growth = row["cells"][growth_column]["text"] if growth_column is not None else ""
            weight = "bold" if row.get("bold") else "normal"
            lines.append(Markup(
                '<tr><td style="padding:3px 12px 3px 0;font-weight:{w}">{name}</td>'
                '<td style="padding:3px 0;text-align:right;font-weight:{w}">{value}</td>'
                '<td style="padding:3px 0 3px 12px;text-align:right;color:#6c757d">{growth}</td></tr>'
            ).format(w=weight, name=row["name"], value=first, growth=growth))
        url = self.get_base_url() + "/odoo/pnl"
        message = escape(self.message or "").replace("\n", Markup("<br/>"))
        return Markup(
            '<div style="font-family:sans-serif;font-size:14px">'
            "<p>{message}</p>"
            '<p style="color:#6c757d">{company} · {period}</p>'
            '<table style="border-collapse:collapse">{lines}</table>'
            '<p><a href="{url}">{open}</a></p>'
            "</div>"
        ).format(
            message=message,
            company=payload["company"],
            period=payload["period"],
            lines=Markup("").join(lines),
            url=url,
            open=_("Open the P&L in Odoo"),
        )

    def action_send_now(self):
        self.ensure_one()
        if self.user_id != self.env.user and not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(_("Only the owner sends this view."))
        self._check_can_schedule()
        if not self._recipient_emails():
            raise UserError(_("Add at least one recipient with an email address."))
        status = self._send_and_record()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": status, "type": "info", "sticky": False},
        }
