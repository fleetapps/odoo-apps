"""Saved views: their period follows the calendar, sharing and ownership,
and the scheduled email rendered as the owner."""

from datetime import datetime, timedelta

from odoo import fields

from odoo.exceptions import AccessError
from odoo.tests import freeze_time, new_test_user, tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestViews(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-08-10", [(cls.revenue, -800.0)])
        cls.post("2026-09-10", [(cls.revenue, -1000.0)])
        cls.colleague = new_test_user(
            cls.env, login="pnl_colleague", email="colleague@example.com",
            groups="base.group_user,account.group_account_readonly",
            company_id=cls.env.company.id, company_ids=[cls.env.company.id])
        cls.accountant.email = "accountant@example.com"

    def report_as(self, user):
        return self.report.with_user(user)

    def test_a_last_month_view_moves_with_the_calendar(self):
        with freeze_time("2026-09-15"):
            saved = self.report_as(self.accountant).save_view({"date": {"filter": "previous_month"}}, "Monthly")
        with freeze_time("2026-10-08"):
            options = self.report_as(self.accountant).get_view_options(saved["id"])
        self.assertEqual(options["date"]["date_from"], "2026-09-01")

    def test_shared_views_are_listed_but_not_changed_by_others(self):
        saved = self.report_as(self.accountant).save_view(self.options(), "Board", shared=True)
        names = [view["name"] for view in self.report_as(self.colleague).list_views()]
        self.assertIn("Board", names)
        with self.assertRaises(AccessError):
            self.report_as(self.colleague).delete_view(saved["id"])
        private = self.report_as(self.accountant).save_view(self.options(), "Mine only")
        self.assertNotIn("Mine only", [view["name"] for view in self.report_as(self.colleague).list_views()])
        self.assertTrue(private["id"])

    def test_making_a_shared_view_ones_default_copies_it(self):
        saved = self.report_as(self.accountant).save_view(self.options(), "Board", shared=True)
        views = self.report_as(self.colleague).set_default_view(saved["id"])
        defaults = [view for view in views if view["is_default"]]
        self.assertEqual(len(defaults), 1)
        self.assertTrue(defaults[0]["mine"])
        self.assertTrue(self.report_as(self.colleague).get_view_options(False))

    def test_only_accountants_schedule_emails(self):
        saved = self.report_as(self.colleague).save_view(self.options(), "Mine")
        view = self.env["odin.pnl.view"].with_user(self.colleague).browse(saved["id"])
        with self.assertRaises(AccessError):
            view.write({"schedule": "monthly", "recipient_emails": "boss@example.com"})

    def test_the_next_sending_time_is_in_the_owners_time_zone(self):
        self.accountant.tz = "Africa/Nairobi"
        view = self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "Monthly", "options": self.options(), "schedule": "monthly", "send_day": 2,
            "send_hour": 7, "recipient_emails": "boss@example.com"})
        # 2 October 07:00 in Nairobi (UTC+3) is 04:00 UTC; from 8 October, next is 2 November.
        self.assertEqual(view._next_send_after(datetime(2026, 10, 8, 12, 0)), datetime(2026, 11, 2, 4, 0))
        self.assertEqual(view._next_send_after(datetime(2026, 10, 1, 12, 0)), datetime(2026, 10, 2, 4, 0))

    def test_the_cron_emails_the_view_as_its_owner(self):
        view = self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "Monthly", "options": self.options(), "schedule": "daily",
            "send_hour": 0, "recipient_emails": "boss@example.com", "send_xlsx": True,
            "message": "Here is the month."})
        view.sudo().next_send = fields.Datetime.now() - timedelta(hours=1)
        # As the scheduler runs it, in its own cursor (tied to the test's).
        with self.enter_registry_test_mode():
            self.env.ref("odin_account_pnl.ir_cron_send_pnl_views").method_direct_trigger()
        mail = self.env["mail.mail"].search([("email_to", "ilike", "boss@example.com")])
        self.assertEqual(len(mail), 1)
        self.assertIn("Sep 2026", mail.subject)
        self.assertIn("1,000.00", mail.body_html)
        self.assertIn("Here is the month.", mail.body_html)
        mimetypes = set(mail.attachment_ids.mapped("mimetype"))
        self.assertIn("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", mimetypes)
        self.assertTrue(view.last_status.startswith("Sent"))
        self.assertGreater(view.next_send, fields.Datetime.now())

    def test_a_view_whose_owner_lost_access_is_not_sent(self):
        view = self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "Monthly", "options": self.options(), "schedule": "daily",
            "recipient_emails": "boss@example.com"})
        self.accountant.group_ids = [(3, self.env.ref("account.group_account_user").id),
                                     (3, self.env.ref("account.group_account_readonly").id)]
        status = view.sudo()._send_and_record()
        self.assertTrue(status.startswith("Not sent"))
        self.assertFalse(self.env["mail.mail"].search([("email_to", "ilike", "boss@example.com")]))
