# -*- coding: utf-8 -*-
"""Scheduled delivery.

The property worth defending hardest: **each email is rendered as its own
recipient**. Rendering once and mailing the result to everybody would be
simpler and faster, and would leak the owner's visibility to every subscriber —
which on a product whose entire premise is per-user permissions is the one bug
that would actually matter.
"""
import json
from unittest.mock import patch

import pytz

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from .test_spec import minimal


@tagged("post_install", "-at_install")
class TestSubscriptions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].create({
            "name": "Sub Owner", "login": "ai_dash_sub_owner",
            "email": "owner@example.com",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.reader = cls.env["res.users"].create({
            "name": "Sub Reader", "login": "ai_dash_sub_reader",
            "email": "reader@example.com",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.board = cls.env["ai.dashboard"].with_user(cls.owner).create({
            "name": "Scheduled board",
            "spec_json": json.dumps(minimal()),
            "state": "published",
            "share_user_ids": [(6, 0, [cls.reader.id])],
        })

    def _sub(self, user):
        return self.env["ai.dashboard.subscription"].with_user(user).create({
            "dashboard_id": self.board.id, "user_id": user.id,
        })

    # ---------------------------------------------------------------- basics
    def test_subscribing_schedules_a_next_send(self):
        sub = self._sub(self.owner)
        self.assertTrue(sub.next_send)

    def test_you_cannot_subscribe_somebody_else(self):
        """Otherwise anyone could fill a colleague's inbox on a schedule."""
        with self.assertRaises(AccessError):
            self.env["ai.dashboard.subscription"].with_user(self.reader).create({
                "dashboard_id": self.board.id, "user_id": self.owner.id,
            })

    def test_one_subscription_per_person_per_dashboard(self):
        self._sub(self.owner)
        with self.assertRaises(Exception):
            self._sub(self.owner)

    def test_deactivating_clears_the_schedule(self):
        sub = self._sub(self.owner)
        sub.active = False
        self.assertFalse(sub.next_send)

    def test_a_subscription_is_private_to_its_owner(self):
        sub = self._sub(self.owner)
        found = self.env["ai.dashboard.subscription"].with_user(
            self.reader).search([("id", "=", sub.id)])
        self.assertFalse(found)

    # ------------------------------------------------------------- rendering
    def test_the_email_is_rendered_as_the_recipient(self):
        """The one that matters. A reader's email must be calculated with the
        reader's permissions, never the owner's."""
        sub = self._sub(self.reader)
        rendered = self.env["ai.dashboard.render"].with_user(
            sub.user_id).render(self.board.id)
        self.assertEqual(rendered["id"], self.board.id)
        self.assertFalse(rendered["is_owner"],
                         "rendered in the reader's right, not the owner's")

    def test_sending_produces_a_mail(self):
        sub = self._sub(self.owner)
        before = self.env["mail.mail"].sudo().search_count([])
        sub.sudo()._send()
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]),
                         before + 1)

    def test_the_email_comes_from_the_company_not_odoobot(self):
        sub = self._sub(self.owner)
        sub.sudo()._send()
        mail = self.env["mail.mail"].sudo().search([("email_to", "=", "owner@example.com")])
        company = self.owner.company_id
        self.assertEqual(mail.author_id, company.partner_id)
        self.assertIn(company.name, mail.email_from)
        self.assertNotIn("OdooBot", mail.email_from)

    def test_the_email_is_sent_from_the_platform_domain_not_the_instance(self):
        """An Odin box as Elora is: served at elora.odin.ist, no alias domain,
        the server's own identity on its host name. Resend refused that, and
        the company's own address (andrew@fleet.ke), with 403: its key is for
        odin.ist."""
        self.env["ir.config_parameter"].set_param("web.base.url", "https://elora.odin.ist")
        company = self.owner.company_id
        company.write({"alias_domain_id": False, "email": "andrew@fleet.ke"})
        self.env["ir.mail_server"].search([]).unlink()
        relay = self.env["ir.mail_server"].create({
            "name": "Resend", "smtp_host": "smtp.resend.com", "from_filter": "noreply@elora.odin.ist"})
        sub = self._sub(self.owner)
        with patch.object(type(self.env["ir.mail_server"]), "_get_default_from_address",
                          lambda server: server.env.company.default_from_email or "noreply@elora.odin.ist"):
            sub.sudo()._send()
        mail = self.env["mail.mail"].sudo().search([("email_to", "=", "owner@example.com")])
        self.assertEqual(mail.email_from, '"%s" <noreply@odin.ist>' % company.name)
        self.assertIn("'Return-Path': 'noreply@odin.ist'", mail.headers)
        self.assertEqual(mail.mail_server_id, relay)

    def test_a_sent_email_leaves_no_mail_behind(self):
        def sent(mails, **kwargs):
            mails.write({"state": "sent"})
        sub = self._sub(self.owner)
        before = self.env["mail.mail"].sudo().search_count([])
        with patch.object(type(self.env["mail.mail"]), "send", sent):
            sub.sudo()._send()
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), before)

    def test_a_refused_email_is_an_error_not_a_send(self):
        def refused(mails, **kwargs):
            mails.write({"state": "exception", "failure_reason": (
                "Mail Delivery Failed\nMail delivery failed via SMTP server 'Resend'.\n"
                "111 Connection refused")})
        sub = self._sub(self.owner)
        sub.sudo().next_send = "2020-01-01 00:00:00"
        # As the scheduler runs it: cron_send commits after each send, which a
        # test cursor allows only in registry test mode.
        with patch.object(type(self.env["mail.mail"]), "send", refused), \
                self.enter_registry_test_mode():
            self.env.ref("ai_dashboards.cron_dashboard_subscriptions").method_direct_trigger()
        self.env.invalidate_all()
        self.assertIn("111 Connection refused", sub.last_error, "the relay's reply, not Odoo's preamble")
        self.assertEqual(sub.send_count, 0)
        mail = self.env["mail.mail"].sudo().search([("email_to", "=", "owner@example.com")])
        self.assertEqual((mail.state, mail.auto_delete), ("exception", True))

    def test_a_recipient_with_no_email_fails_loudly(self):
        self.owner.partner_id.email = False
        sub = self._sub(self.owner)
        with self.assertRaises(Exception):
            sub.sudo()._send()

    # ------------------------------------------------------------------ cron
    def test_the_cron_reschedules_even_after_a_failure(self):
        """A dashboard that errors every week should keep trying and keep
        saying so, not silently stop."""
        sub = self._sub(self.owner)
        self.owner.partner_id.email = False   # force _send to raise
        sub.sudo().next_send = "2020-01-01 00:00:00"
        # As the scheduler runs it: cron_send commits after each send.
        with self.enter_registry_test_mode():
            self.env.ref("ai_dashboards.cron_dashboard_subscriptions").method_direct_trigger()
        self.env.invalidate_all()
        self.assertTrue(sub.last_error)
        self.assertTrue(sub.next_send > sub.create_date)

    def test_the_cron_only_takes_what_is_due(self):
        sub = self._sub(self.owner)
        sub.sudo().next_send = "2999-01-01 00:00:00"
        before = self.env["mail.mail"].sudo().search_count([])
        self.env["ai.dashboard.subscription"].sudo().cron_send()
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), before)


@tagged("post_install", "-at_install")
class TestScheduleTimezone(TransactionCase):
    """The hour the email actually lands.

    pytz attaches the offset in force at *that moment*, so arithmetic on an
    aware datetime carries January's offset onto a July date. That shipped as
    an email arriving at 08:00 for half the year and 07:00 for the other half,
    with nothing in the logs and no way for a recipient to describe the fault.
    """

    def _sub_at(self, tz_name, hour=7):
        user = self.env["res.users"].create({
            "name": "TZ %s" % tz_name, "login": "ai_tz_%s" % abs(hash(tz_name)),
            "email": "tz@example.com", "tz": tz_name,
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        board = self.env["ai.dashboard"].create({
            "name": "TZ board %s" % tz_name,
            "spec_json": json.dumps(minimal()),
            "state": "published",
        })
        return self.env["ai.dashboard.subscription"].create({
            "dashboard_id": board.id, "user_id": user.id, "send_hour": hour,
            "interval": "daily",
        })

    def test_the_send_hour_survives_a_dst_boundary(self):
        sub = self._sub_at("Europe/London")
        for base in ("2026-01-15 12:00:00", "2026-07-15 12:00:00"):
            sub._schedule_next(after=fields.Datetime.to_datetime(base))
            landed = pytz.utc.localize(sub.next_send).astimezone(
                pytz.timezone("Europe/London"))
            self.assertEqual(
                landed.hour, 7,
                "scheduled from %s it lands at %s local, not 07:00"
                % (base, landed.strftime("%H:%M")))

    def test_a_user_with_no_timezone_falls_back_to_utc(self):
        sub = self._sub_at("Europe/London")
        sub.user_id.tz = False
        sub._schedule_next()
        self.assertTrue(sub.next_send, "no timezone must not mean no schedule")
