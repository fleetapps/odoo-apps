# -*- coding: utf-8 -*-
"""Scheduled delivery: the dashboard comes to you.

Makes a dashboard useful to people who never open Odoo — which is most of the
people who want the numbers in it.

The property that matters more than the schedule: **each email is rendered as
its own recipient.** Not as the dashboard's owner, not as the cron user. A
subscription is a standing instruction to run somebody's own query on their
behalf, so the figures in Wednesday's email are the figures that person would
have seen had they opened it themselves. Rendering once and mailing the result
to everybody would be simpler, faster, and would leak the owner's visibility to
every subscriber - which on a per-user-permissions product is the one bug that
would matter.
"""
import logging

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

# How many subscriptions one cron pass will send. A cap so a database with
# hundreds does not hold the worker for the whole hour; the rest go next pass.
BATCH = 50


class AIDashboardSubscription(models.Model):
    _name = "ai.dashboard.subscription"
    _description = "AI Dashboard Subscription"
    _order = "next_send"
    _rec_name = "dashboard_id"

    dashboard_id = fields.Many2one(
        "ai.dashboard", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", index=True,
        default=lambda self: self.env.user,
        help="Who receives it, and — just as importantly — whose permissions "
             "the figures are calculated with.")
    active = fields.Boolean(default=True)

    interval = fields.Selection(
        [("daily", "Every weekday morning"),
         ("weekly", "Every Monday morning"),
         ("monthly", "First of the month")],
        default="weekly", required=True)
    send_hour = fields.Integer(
        default=7, help="Hour of the day, in your timezone, to send it.")

    next_send = fields.Datetime(readonly=True, index=True)
    last_sent = fields.Datetime(readonly=True)
    send_count = fields.Integer(readonly=True, default=0)
    last_error = fields.Char(readonly=True)

    _one_per_user = models.Constraint(
        "UNIQUE (dashboard_id, user_id)",
        "You already have this dashboard scheduled.")

    @api.constrains("send_hour")
    def _check_send_hour(self):
        for rec in self:
            if not 0 <= rec.send_hour <= 23:
                raise ValidationError(_(
                    "Pick an hour between 0 and 23 — %s is not one.")
                    % rec.send_hour)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            # Subscribing to somebody else's dashboard is fine — you can read
            # it — but subscribing *somebody else* to one is not: it would let
            # anyone fill a colleague's inbox on a schedule.
            if rec.user_id.id != self.env.uid and not self.env.su \
                    and not self.env.user.has_group(
                        "ai_dashboards.group_dashboard_admin"):
                raise AccessError(_(
                    "You can only schedule a dashboard for yourself."))
            rec.dashboard_id.check_access("read")
            rec._schedule_next()
        return records

    def write(self, vals):
        result = super().write(vals)
        if {"interval", "send_hour", "active"} & set(vals):
            self._schedule_next()
        return result

    # ------------------------------------------------------------- schedule
    def _schedule_next(self, after=None):
        """Work out the next send, in the subscriber's own timezone.

        Timezone matters here in a way it does not elsewhere in this module: a
        "Monday morning" email that lands at 2am is worse than no email, and
        the subscriber's timezone is the only one that makes the promise true.
        """
        for rec in self:
            if not rec.active:
                rec.next_send = False
                continue
            user_tz = rec.user_id.tz or "UTC"
            now = fields.Datetime.context_timestamp(
                rec.with_context(tz=user_tz), after or fields.Datetime.now())
            nxt = now.replace(hour=rec.send_hour, minute=0, second=0,
                              microsecond=0)
            if nxt <= now:
                nxt += relativedelta(days=1)
            if rec.interval == "daily":
                # Weekday mornings: a Saturday report nobody reads until Monday
                # is just an unread email.
                while nxt.weekday() >= 5:
                    nxt += relativedelta(days=1)
            elif rec.interval == "weekly":
                while nxt.weekday() != 0:
                    nxt += relativedelta(days=1)
            elif rec.interval == "monthly":
                if nxt.day != 1:
                    nxt = (nxt + relativedelta(months=1)).replace(day=1)
            rec.next_send = nxt.astimezone(pytz.utc).replace(tzinfo=None)

    # ----------------------------------------------------------------- cron
    @api.model
    def cron_send(self):
        """Send whatever is due. Never lets one failure stop the rest."""
        due = self.sudo().search(
            [("active", "=", True), ("next_send", "!=", False),
             ("next_send", "<=", fields.Datetime.now())], limit=BATCH)
        _logger.info("AI Dashboards: %s subscription(s) due", len(due))
        for sub in due:
            try:
                sub._send()
                sub.write({
                    "last_sent": fields.Datetime.now(),
                    "send_count": sub.send_count + 1,
                    "last_error": False,
                })
            except Exception as exc:  # noqa: BLE001 - one bad send, not all
                _logger.exception("AI Dashboards: subscription %s failed",
                                  sub.id)
                sub.last_error = str(exc)[:200]
            finally:
                # Rescheduled either way: a dashboard that errors every week
                # should keep trying and keep saying so, not silently stop.
                sub._schedule_next(after=fields.Datetime.now())
            self.env.cr.commit()  # each send is independent work

    def _send(self):
        self.ensure_one()
        user = self.user_id
        if not user.partner_id.email:
            raise UserError(_("%s has no email address.") % user.name)

        # THE line that matters: rendered as the recipient, so the email holds
        # the figures they would have seen, not the owner's.
        rendered = self.env["ai.dashboard.render"].with_user(user).render(
            self.dashboard_id.id)

        body = self.env["ir.qweb"]._render(
            "ai_dashboards.dashboard_email", {
                "dashboard": self.dashboard_id,
                "data": rendered,
                "url": self._url(),
                "subscription": self,
                "fmt": self._format_value,
            })
        self.env["mail.mail"].sudo().create({
            "subject": _("%s — your dashboard") % self.dashboard_id.name,
            "email_to": user.partner_id.email,
            "author_id": self.env.company.partner_id.id,
            "body_html": body,
            "auto_delete": True,
        }).send()
        _logger.info("AI Dashboards: sent '%s' to %s",
                     self.dashboard_id.name, user.login)

    def _format_value(self, value, widget, currency):
        """Numbers in an email have to be readable without any JavaScript."""
        if value is None:
            return "—"
        fmt = (widget or {}).get("format") or {}
        kind = fmt.get("kind", "plain")
        decimals = fmt.get("decimals")
        if decimals is None:
            decimals = 0 if kind == "integer" else (
                currency.get("decimals", 2) if kind == "monetary" else 2)
        try:
            text = "{:,.{d}f}".format(float(value), d=decimals)
        except (TypeError, ValueError):
            return str(value)
        if kind == "monetary" and currency.get("symbol"):
            return ("%s%s" % (currency["symbol"], text)
                    if currency.get("position") == "before"
                    else "%s%s" % (text, currency["symbol"]))
        if kind == "percent":
            return "%s%%" % text
        return "%s%s" % (text, fmt.get("suffix", ""))

    def _url(self):
        from odoo.addons.mcp_governance_suite.models.mcp_url import (
            public_base_url,
        )
        return "%s/odoo/action-ai_dashboards.action_dashboards/%s" % (
            public_base_url(self.env), self.dashboard_id.id)

    # -------------------------------------------------------------- actions
    def action_send_now(self):
        """Send one immediately, so nobody has to wait until Monday to find
        out whether the schedule works."""
        for rec in self:
            rec._send()
            rec.write({"last_sent": fields.Datetime.now(),
                       "send_count": rec.send_count + 1, "last_error": False})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Sent"),
                "message": _("Check your inbox — it went to %s.")
                % self.user_id.partner_id.email,
                "type": "success",
            },
        }
