# -*- coding: utf-8 -*-
"""Sharing a dashboard, and emailing it to the people it is shared with.

The rules these hold:

* a dashboard's owner may schedule it for anyone who can open it — and for
  nobody else, because each email is calculated as its recipient;
* anyone else may schedule only themselves;
* the owner sees every schedule on their dashboards, a recipient sees their
  own, and nobody else sees who receives what;
* a recipient can always pause or delete an email, whoever set it up;
* taking someone's access away pauses their email rather than leaving it to
  fail every week.
"""
import json

import pytz

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from .test_spec import minimal


@tagged("post_install", "-at_install")
class TestSharing(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = [cls.env.ref("base.group_user").id,
                  cls.env.ref("ai_dashboards.group_dashboard_user").id]
        make = lambda login: cls.env["res.users"].create({
            "name": login.title(), "login": "ai_share_%s" % login,
            "email": "%s@example.com" % login, "tz": "Africa/Nairobi",
            "group_ids": [(6, 0, groups)],
        })
        cls.owner = make("owner")
        cls.colleague = make("colleague")
        cls.outsider = make("outsider")
        cls.board = cls.env["ai.dashboard"].with_user(cls.owner).create({
            "name": "Shared board", "spec_json": json.dumps(minimal()),
            "state": "published",
            "share_user_ids": [(6, 0, [cls.colleague.id])],
        })
        cls.Sub = cls.env["ai.dashboard.subscription"]

    def _schedule(self, by, to, **values):
        return self.Sub.with_user(by).create(dict(
            {"dashboard_id": self.board.id, "user_id": to.id}, **values))

    # ------------------------------------------------------------- who may
    def test_the_owner_can_schedule_someone_it_is_shared_with(self):
        sub = self._schedule(self.owner, self.colleague)
        self.assertEqual(sub.created_by_id, self.owner)
        self.assertTrue(sub.next_send)

    def test_the_owner_cannot_schedule_someone_who_cannot_open_it(self):
        with self.assertRaises(AccessError):
            self._schedule(self.owner, self.outsider)

    def test_a_reader_can_schedule_only_themselves(self):
        self._schedule(self.colleague, self.colleague)
        with self.assertRaises(AccessError):
            self._schedule(self.colleague, self.owner)

    def test_who_can_be_emailed_is_who_can_open_it(self):
        allowed = self.board.allowed_recipient_ids
        self.assertIn(self.owner, allowed)
        self.assertIn(self.colleague, allowed)
        self.assertNotIn(self.outsider, allowed)
        draft = self.env["ai.dashboard"].with_user(self.owner).create({
            "name": "Draft", "spec_json": json.dumps(minimal()),
            "share_user_ids": [(6, 0, [self.colleague.id])]})
        self.assertEqual(draft.allowed_recipient_ids, self.owner)

    # ------------------------------------------------------------ who sees
    def test_the_owner_sees_every_schedule_on_their_dashboard(self):
        mine = self._schedule(self.owner, self.owner)
        theirs = self._schedule(self.colleague, self.colleague)
        seen = self.board.with_user(self.owner).schedule_ids
        self.assertEqual(seen, mine | theirs)

    def test_a_recipient_sees_only_their_own(self):
        self._schedule(self.owner, self.owner)
        theirs = self._schedule(self.owner, self.colleague)
        self.assertEqual(self.Sub.with_user(self.colleague).search([]), theirs)
        self.assertFalse(self.Sub.with_user(self.outsider).search([]))

    def test_paused_schedules_are_still_listed(self):
        sub = self._schedule(self.owner, self.colleague)
        sub.with_user(self.owner).active = False
        self.assertIn(sub, self.board.with_user(self.owner).schedule_ids)

    # --------------------------------------------------- the recipient's say
    def test_a_recipient_can_pause_and_delete_what_the_owner_set_up(self):
        sub = self._schedule(self.owner, self.colleague)
        sub.with_user(self.colleague).write({"active": False})
        self.assertFalse(sub.next_send)
        sub.with_user(self.colleague).unlink()
        self.assertFalse(sub.exists())

    def test_the_owner_can_delete_a_schedule_on_their_dashboard(self):
        sub = self._schedule(self.colleague, self.colleague)
        sub.with_user(self.owner).unlink()
        self.assertFalse(sub.exists())

    # ------------------------------------------------------- losing access
    def test_taking_access_away_pauses_their_email(self):
        sub = self._schedule(self.owner, self.colleague)
        self.board.with_user(self.owner).write(
            {"share_user_ids": [(3, self.colleague.id)]})
        self.assertFalse(sub.active)
        self.assertTrue(sub.paused_reason)
        with self.assertRaises(AccessError):
            sub.with_user(self.owner).write({"active": True})

    def test_switching_back_on_clears_the_reason(self):
        sub = self._schedule(self.owner, self.colleague)
        self.board.with_user(self.owner).write(
            {"share_user_ids": [(3, self.colleague.id)]})
        self.board.with_user(self.owner).write(
            {"share_user_ids": [(4, self.colleague.id)]})
        sub.with_user(self.owner).write({"active": True})
        self.assertTrue(sub.active)
        self.assertFalse(sub.paused_reason)

    # ------------------------------------------------------------ schedule
    def test_a_weekly_email_goes_out_on_its_weekday(self):
        sub = self._schedule(self.owner, self.colleague,
                             interval="weekly", weekday="2", send_hour=8)
        local = pytz.utc.localize(sub.next_send).astimezone(
            pytz.timezone("Africa/Nairobi"))
        self.assertEqual(local.weekday(), 2)
        self.assertEqual(local.hour, 8)

    def test_the_time_reads_as_a_time(self):
        sub = self._schedule(self.owner, self.colleague, send_hour=7)
        self.assertEqual(sub.send_time, "7")
        sub.send_time = "9"
        self.assertEqual(sub.send_hour, 9)

    # --------------------------------------------------------- the actions
    def test_only_the_owner_opens_the_share_dialog(self):
        action = self.board.with_user(self.owner).action_open_sharing()
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["views"][0][0],
                         self.env.ref("ai_dashboards.ai_dashboard_view_form_share").id)
        with self.assertRaises(AccessError):
            self.board.with_user(self.colleague).action_open_sharing()

    def test_archiving_goes_back_to_the_list(self):
        action = self.board.with_user(self.owner).action_discard()
        self.assertEqual(action["res_model"], "ai.dashboard")
        self.assertFalse(self.board.active)
