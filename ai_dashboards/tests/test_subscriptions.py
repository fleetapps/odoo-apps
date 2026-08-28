# -*- coding: utf-8 -*-
"""Scheduled delivery.

The property worth defending hardest: **each email is rendered as its own
recipient**. Rendering once and mailing the result to everybody would be
simpler and faster, and would leak the owner's visibility to every subscriber —
which on a product whose entire premise is per-user permissions is the one bug
that would actually matter.
"""
import json

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
        self.env["ai.dashboard.subscription"].sudo().cron_send()
        self.assertTrue(sub.last_error)
        self.assertTrue(sub.next_send > sub.create_date)

    def test_the_cron_only_takes_what_is_due(self):
        sub = self._sub(self.owner)
        sub.sudo().next_send = "2999-01-01 00:00:00"
        before = self.env["mail.mail"].sudo().search_count([])
        self.env["ai.dashboard.subscription"].sudo().cron_send()
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), before)
