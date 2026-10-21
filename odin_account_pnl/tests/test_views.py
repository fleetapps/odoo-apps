"""Saved views: their period follows the calendar, sharing and ownership,
and the scheduled email rendered as the owner."""

from datetime import datetime, timedelta
from unittest.mock import patch

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

    def test_the_default_view_comes_with_its_id(self):
        """So the page can name the view it opens on."""
        self.assertFalse(self.report_as(self.accountant).get_default_view())
        saved = self.report_as(self.accountant).save_view(self.options(), "Board")
        self.report_as(self.accountant).set_default_view(saved["id"])
        view = self.report_as(self.accountant).get_default_view()
        self.assertEqual(view["id"], saved["id"])
        self.assertEqual(view["options"]["date"]["date_from"], "2026-09-01")

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
        # Sending is off under tests, so the mail is still queued.
        self.assertEqual(mail.state, "outgoing")
        self.assertTrue(view.last_status.startswith("Queued"))
        self.assertEqual(mail.author_id, self.accountant.partner_id, "the Reply-To is named after the owner")
        self.assertGreater(view.next_send, fields.Datetime.now())

    def test_a_view_made_from_the_menu_saves_and_sends(self):
        """The form carries no options field -- they come from the report -- so
        pressing New there creates a view with nothing in `options`. That used
        to hit the NOT NULL behind `required=True` (fields.Json stores an empty
        dict as NULL) and "Send now" died on "Missing required value for the
        field 'Options'". Such a view is simply the default P&L."""
        view = self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "From the menu", "schedule": "daily",
            "recipient_emails": "boss@example.com"})
        self.assertFalse(view.options, "stored as NULL, which every reader takes as {}")
        self.assertTrue(view.period_rule, "the period still has a label")
        sent = []
        with patch.object(type(self.env["mail.mail"]), "send",
                          lambda mails, **kwargs: sent.append(mails) or mails.write({"state": "sent"})):
            action = view.with_user(self.accountant).action_send_now()
        self.assertEqual(action["params"]["type"], "info", view.last_status)
        self.assertEqual(len(sent), 1, "the default P&L was rendered and sent")

    def _schedule(self):
        return self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "Monthly", "options": self.options(), "schedule": "daily",
            "recipient_emails": "boss@example.com"})

    def _mails(self):
        return self.env["mail.mail"].search([("email_to", "ilike", "boss@example.com")])

    def test_a_sent_email_says_so_and_leaves_no_mail_behind(self):
        def sent(mails, **kwargs):
            mails.write({"state": "sent"})
        view = self._schedule()
        with patch.object(type(self.env["mail.mail"]), "send", sent):
            status = view.sudo()._send_and_record()
        self.assertEqual(status, "Sent to 1 recipient(s)")
        self.assertFalse(self._mails(), "removed once sent, as auto_delete would")

    def test_a_refused_email_is_reported_and_kept_for_a_retry(self):
        def refused(mails, **kwargs):
            # Odoo's own wording around the relay's reply, as mail.mail stores it.
            mails.write({"state": "exception", "failure_reason": (
                "Mail Delivery Failed\nMail delivery failed via SMTP server 'Resend'.\n"
                "SMTPDataError: (550, b'not authorized to send emails from gmail.com')")})
        self._as_odin_instance()
        view = self._schedule()
        with patch.object(type(self.env["mail.mail"]), "send", refused):
            action = view.with_user(self.accountant).action_send_now()
        self.assertEqual(
            view.last_status,
            "Not sent from noreply@odin.ist: SMTPDataError: (550, b'not authorized to send emails from gmail.com')",
            "which sender was refused, and the relay's own reason")
        self.assertEqual(action["params"]["type"], "warning")
        mail = self._mails()
        self.assertEqual((mail.state, mail.auto_delete), ("exception", True),
                         "kept for the mail queue's retry, deleted once that sends it")

    def test_a_view_whose_owner_lost_access_is_not_sent(self):
        view = self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "Monthly", "options": self.options(), "schedule": "daily",
            "recipient_emails": "boss@example.com"})
        self.accountant.group_ids = [(3, self.env.ref("account.group_account_user").id),
                                     (3, self.env.ref("account.group_account_readonly").id)]
        status = view.sudo()._send_and_record()
        self.assertTrue(status.startswith("Not sent"))
        self.assertFalse(self.env["mail.mail"].search([("email_to", "ilike", "boss@example.com")]))

    def _as_odin_instance(self):
        """An Odin box as Elora is: served at elora.odin.ist, a company with
        no alias domain, and the server's own identity on its host name."""
        self.env["ir.config_parameter"].set_param("web.base.url", "https://elora.odin.ist")
        self.env.company.alias_domain_id = False
        self.env["ir.mail_server"].search([]).unlink()
        patcher = patch.object(type(self.env["ir.mail_server"]), "_get_default_from_address",
                               lambda server: server.env.company.default_from_email or "noreply@elora.odin.ist")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_email_is_sent_from_the_platform_domain_not_the_instance(self):
        """Resend refused noreply@elora.odin.ist with 403 "not authorized to
        send emails from elora.odin.ist"; its key is for odin.ist."""
        self._as_odin_instance()
        relay = self.env["ir.mail_server"].create({
            "name": "Resend", "smtp_host": "smtp.resend.com", "from_filter": "noreply@elora.odin.ist"})
        self._schedule().sudo()._send_and_record()
        mail = self._mails()
        self.assertEqual(mail.email_from, f'"{self.env.company.name}" <noreply@odin.ist>')
        self.assertIn("'Return-Path': 'noreply@odin.ist'", mail.headers, "the envelope sender too")
        self.assertEqual(mail.mail_server_id, relay,
                         "named on the mail, so Odoo does not put the host-name address back in the From")

    def test_a_relay_filtering_on_a_domain_sends_from_noreply_there(self):
        self._as_odin_instance()
        self.env["ir.mail_server"].create({"name": "Resend", "smtp_host": "smtp.resend.com", "from_filter": "odin.ist"})
        self.accountant.email = "accountant@gmail.com"
        with patch.object(type(self.env["ir.mail_server"]), "_get_default_from_address", lambda server: None):
            self._schedule().sudo()._send_and_record()
        self.assertEqual(self._mails().email_from, f'"{self.env.company.name}" <noreply@odin.ist>',
                         "not the owner's gmail address, which the relay refuses")

    def test_an_alias_domain_is_sent_from_as_it_is(self):
        """The company's alias domain is a deliberate choice: BizSure sends as
        noreply@odin.ist from it, as every other app there does."""
        self._as_odin_instance()
        self.env.company.alias_domain_id = self.env["mail.alias.domain"].create(
            {"name": "acme.example", "default_from": "noreply"})
        self._schedule().sudo()._send_and_record()
        self.assertEqual(self._mails().email_from, f'"{self.env.company.name}" <noreply@acme.example>')

    def test_a_recipient_listed_twice_gets_one_email(self):
        boss = self.env["res.partner"].create({"name": "Boss", "email": "boss@example.com"})
        view = self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "Monthly", "options": self.options(), "schedule": "daily",
            "recipient_ids": [(6, 0, boss.ids)], "recipient_emails": "BOSS@example.com, cfo@example.com"})
        status = view.sudo()._send_and_record()
        self.assertEqual(self._mails().email_to, '"Boss" <boss@example.com>, cfo@example.com')
        self.assertEqual(status, "Queued for 2 recipient(s)")
