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
            "send_hour": "7", "recipient_emails": "boss@example.com"})
        # 2 October 07:00 in Nairobi (UTC+3) is 04:00 UTC; from 8 October, next is 2 November.
        self.assertEqual(view._next_send_after(datetime(2026, 10, 8, 12, 0)), datetime(2026, 11, 2, 4, 0))
        self.assertEqual(view._next_send_after(datetime(2026, 10, 1, 12, 0)), datetime(2026, 10, 2, 4, 0))

    def test_the_cron_emails_the_view_as_its_owner(self):
        view = self.env["odin.pnl.view"].with_user(self.accountant).create({
            "name": "Monthly", "options": self.options(), "schedule": "daily",
            "send_hour": "0", "recipient_emails": "boss@example.com", "send_xlsx": True,
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
        self.assertEqual(action["params"]["type"], "success", view.last_status)
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

    # ---- the schedule ------------------------------------------------------

    def test_every_weekday_skips_the_weekend(self):
        view = self._schedule()
        view.write({"schedule": "weekdays", "send_hour": "7"})
        # Friday 9 October 2026 at noon UTC: Saturday and Sunday are skipped.
        self.assertEqual(view._next_send_after(datetime(2026, 10, 9, 12, 0)), datetime(2026, 10, 12, 7, 0))
        self.assertEqual(view._next_send_after(datetime(2026, 10, 7, 6, 0)), datetime(2026, 10, 7, 7, 0))

    def test_every_quarter_sends_after_each_quarter(self):
        view = self._schedule()
        view.write({"schedule": "quarterly", "send_day": 5, "send_hour": "8"})
        self.assertEqual(view._next_send_after(datetime(2026, 10, 8, 12, 0)), datetime(2027, 1, 5, 8, 0))
        self.assertEqual(view._next_send_after(datetime(2026, 9, 1, 0, 0)), datetime(2026, 10, 5, 8, 0))
        self.assertEqual(view._next_send_after(datetime(2026, 4, 5, 7, 0)), datetime(2026, 4, 5, 8, 0))
        self.assertIn("Every quarter on day 5", view.schedule_summary)

    def test_the_schedule_reads_in_words(self):
        self.accountant.tz = "Africa/Nairobi"
        view = self._schedule()
        view.write({"schedule": "weekly", "send_weekday": "4", "send_hour": "9"})
        self.assertEqual(view.schedule_summary, "Every Friday at 09:00 (Africa/Nairobi)")
        view.with_user(self.accountant).action_toggle_pause()
        self.assertTrue(view.schedule_paused)
        self.assertTrue(view.schedule_summary.startswith("Paused: "))

    def test_a_paused_view_is_not_sent_and_resumes_afresh(self):
        view = self._schedule()
        view.sudo().write({"failure_count": 2})
        view.with_user(self.accountant).action_toggle_pause()
        self.assertFalse(view.next_send, "nothing planned while paused")
        view.sudo().write({"next_send": fields.Datetime.now() - timedelta(hours=1)})
        with self.enter_registry_test_mode():
            self.env.ref("odin_account_pnl.ir_cron_send_pnl_views").method_direct_trigger()
        self.assertFalse(self._mails(), "the cron leaves a paused view alone")
        view.with_user(self.accountant).action_toggle_pause()
        self.assertFalse(view.schedule_paused)
        self.assertEqual(view.failure_count, 0, "a fresh start")
        self.assertTrue(view.next_send)

    def test_a_test_goes_to_oneself_and_leaves_the_schedule_alone(self):
        view = self._schedule()
        action = view.with_user(self.accountant).action_send_test()
        mail = self.env["mail.mail"].search([("email_to", "ilike", "accountant@example.com")])
        self.assertEqual(len(mail), 1)
        self.assertTrue(mail.subject.startswith("[Test] "))
        self.assertFalse(self._mails(), "the recipients get nothing")
        self.assertFalse(view.last_sent, "the schedule did not run")
        self.assertEqual(view.log_ids.kind, "test")
        self.assertIn("Test:", action["params"]["message"])

    def test_three_failed_sends_in_a_row_pause_the_schedule(self):
        def refused(mails, **kwargs):
            mails.write({"state": "exception", "failure_reason": "SMTPDataError: (550, b'no')"})
        view = self._schedule()
        with patch.object(type(self.env["mail.mail"]), "send", refused):
            for _attempt in range(3):
                view.sudo()._send_and_record()
        self.assertTrue(view.schedule_paused)
        self.assertTrue(view.last_failed)
        self.assertIn("Paused after 3 failed sends", view.last_status)
        self.assertEqual(len(view.log_ids), 3)
        self.assertTrue(all(view.log_ids.mapped("failed")))
        # Sent again by hand, and it goes: the failure is cleared.
        with patch.object(type(self.env["mail.mail"]), "send", lambda mails, **kw: mails.write({"state": "sent"})):
            view.with_user(self.accountant).action_send_now()
        self.assertFalse(view.last_failed)
        self.assertEqual(view.failure_count, 0)

    def test_the_subject_takes_the_period(self):
        view = self._schedule()
        view.write({"subject": "{company} results for {period}"})
        view.sudo()._send_and_record()
        self.assertEqual(self._mails().subject, f"{self.env.company.name} results for Sep 2026")

    def test_the_email_shows_the_figures_or_only_the_note(self):
        view = self._schedule()
        view.write({"options": self.options(comparison={"mode": "previous_period", "periods": 1}),
                    "message": "Month closed."})
        view.sudo()._send_and_record()
        body = self._mails().body_html
        self.assertIn("1,000.00", body)
        self.assertIn("vs Aug 2026", body, "the headline figures and their change")
        self.assertIn(f"/odoo/pnl?pnl_view={view.id}", body, "a link to the view itself")
        self._mails().sudo().unlink()
        view.write({"include_summary": False})
        view.sudo()._send_and_record()
        body = self._mails().body_html
        self.assertNotIn("1,000.00", body, "figures in the attachments only")
        self.assertIn("Month closed.", body)

    def test_the_schedule_dialog_opens_ones_own_view_or_a_new_one(self):
        report = self.report_as(self.accountant)
        saved = report.save_view(self.options(), "Board")
        action = report.action_schedule(self.options(), saved["id"])
        self.assertEqual(action["res_id"], saved["id"])
        self.assertEqual(action["target"], "new")
        # Someone else's shared view: a new view of one's own, monthly.
        shared = self.report_as(self.colleague).save_view(self.options(), "Team", shared=True)
        action = report.action_schedule({"date": {"filter": "previous_month"}}, shared["id"])
        self.assertNotIn("res_id", action)
        self.assertEqual(action["context"]["default_name"], "Team")
        self.assertEqual(action["context"]["default_schedule"], "monthly")
        self.assertEqual(action["context"]["default_options"]["date"]["filter"], "previous_month")
        with self.assertRaises(AccessError):
            self.report_as(self.colleague).action_schedule(self.options(), shared["id"])

    def test_a_private_view_of_someone_else_opens_the_default_report(self):
        private = self.report_as(self.accountant).save_view(self.options(), "Mine")
        self.assertFalse(self.report_as(self.colleague).get_view_options(private["id"]))
        self.assertTrue(self.report_as(self.accountant).get_view_options(private["id"]))

    def test_the_list_marks_paused_and_failing_schedules(self):
        view = self._schedule()
        view.sudo().write({"last_failed": True, "last_status": "Not sent: nope"})
        listed = next(item for item in self.report_as(self.accountant).list_views() if item["id"] == view.id)
        self.assertTrue(listed["failed"])
        self.assertFalse(listed["paused"])
        self.assertEqual(listed["status"], "Not sent: nope")
        self.assertTrue(listed["schedule"].startswith("Every day"))
