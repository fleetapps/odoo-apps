from datetime import date

from dateutil.relativedelta import relativedelta
from email.utils import formataddr

from odoo.tests import new_test_user, tagged

from .common import MedicalQuoteCase


@tagged("post_install", "-at_install")
class TestRenewals(MedicalQuoteCase):
    """A policy sold is remembered for its renewal; the person responsible
    is reminded 30 days, 7 days and on the day, once each; a renewal not
    renewed lapses; quoting and accepting the renewal closes it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Renewal = cls.env["odin.renewal"]
        cls.agent.email = "agent@example.com"
        cls.manager = new_test_user(
            cls.env, login="renewals.manager", name="Renewals Manager",
            email="manager@example.com", groups="sales_team.group_sale_manager")
        cls.company.odin_renewal_digest_user_id = cls.manager

    def sell(self, cover_start=date(2026, 3, 1)):
        saved = self.Quote.save_quote({
            "customer": {"source": "new", "name": "Henry", "phone": "0798726720"},
            "cover": self.cover(),
            "plans": [{"template_id": self.jamii.id, "options": []}],
        })
        comparison = self.env["odin.quote.comparison"].with_user(self.agent).browse(
            saved["comparison"]["id"])
        action = comparison.action_odin_accept()
        wizard = self.env["odin.medical.quote.accept"].with_user(self.agent).with_context(
            **action["context"]).create({"payment": "later", "cover_start": cover_start})
        wizard.action_accept()
        return comparison

    def reminders(self, renewal):
        return self.env["mail.mail"].search([("model", "=", "odin.renewal"), ("res_id", "=", renewal.id)])

    def test_sold_policy_renews_a_year_on(self):
        comparison = self.sell()
        renewal = comparison.odin_next_renewal_id
        self.assertTrue(renewal)
        self.assertEqual(renewal.renewal_date, date(2027, 3, 1))
        self.assertEqual(renewal.origin, "ours")
        self.assertEqual(renewal.insurer_id, self.insurer_jamii)
        self.assertEqual(renewal.partner_id, comparison.partner_id)
        self.assertEqual(renewal.user_id, self.agent)
        self.assertEqual(renewal.source_lead_id, comparison.opportunity_id)
        self.assertFalse(renewal.start_to_confirm)
        self.assertEqual(renewal.state, "upcoming")
        self.assertFalse(renewal.lead_id, "no opportunity a year early")

    def test_cover_placed(self):
        comparison = self.sell()
        placed = self.env["odin.renewal.placed"].with_user(self.agent).with_context(
            default_comparison_id=comparison.id).create({"policy_number": "APA/123",
                                                          "cover_start": date(2026, 3, 15)})
        placed.action_confirm()
        renewal = comparison.odin_next_renewal_id
        self.assertEqual(renewal.policy_number, "APA/123")
        self.assertEqual(renewal.renewal_date, date(2027, 3, 15))
        self.assertFalse(comparison.odin_placement_pending, "the Place cover to-do is done")

    def test_reminders_once_each(self):
        renewal = self.sell().odin_next_renewal_id
        remind = self.Renewal._cron_remind
        remind(today=date(2027, 1, 1))
        self.assertFalse(self.reminders(renewal), "nothing two months before")
        remind(today=date(2027, 1, 30))  # 30 days before
        self.assertEqual(len(self.reminders(renewal)), 1)
        self.assertEqual(renewal.state, "in_progress")
        lead = renewal.lead_id
        self.assertTrue(lead, "the renewal opportunity is opened")
        self.assertNotEqual(lead, renewal.source_lead_id)
        self.assertEqual(lead.date_deadline, date(2027, 3, 1))
        self.assertIn("Renewal", lead.tag_ids.mapped("name"))
        self.assertTrue(lead.activity_ids, "with its to-do")
        remind(today=date(2027, 1, 30))
        self.assertEqual(len(self.reminders(renewal)), 1, "a second run sends nothing")
        remind(today=date(2027, 2, 25))  # 4 days before: the 7-day reminder
        self.assertEqual(len(self.reminders(renewal)), 2)
        remind(today=date(2027, 3, 1))
        self.assertEqual(len(self.reminders(renewal)), 3)
        remind(today=date(2027, 3, 2))
        mails = self.reminders(renewal).sorted("id")
        self.assertEqual(len(mails), 4)
        self.assertIn(self.manager.partner_id, mails[-1].recipient_ids,
                      "overdue goes to the manager too")
        self.assertIn(self.agent.partner_id, mails[-1].recipient_ids)
        remind(today=date(2027, 3, 31))
        self.assertEqual(renewal.state, "lapsed")
        self.assertFalse(lead.active, "its opportunity is lost")
        self.assertTrue(renewal.source_lead_id.active, "the sale it came from is not")

    def test_sender(self):
        self.company.alias_domain_id = self.env["mail.alias.domain"].create({
            "name": "odin.ist", "default_from": "noreply"})
        self.assertEqual(self.Renewal._odin_mail_from(self.company),
                         formataddr((self.company.name, "noreply@odin.ist")))

    def test_late_renewal_skips_old_reminders(self):
        renewal = self.Renewal.create({
            "partner_id": self.env["res.partner"].create({"name": "Late"}).id,
            "renewal_date": date(2027, 3, 1), "user_id": self.agent.id})
        self.Renewal._cron_remind(today=date(2027, 2, 26))
        self.assertEqual(len(self.reminders(renewal)), 1, "only the 7-day one, not the 30")
        self.assertEqual(set(renewal.reminders_sent.split(",")), {"30", "7"})

    def test_prospect_lead(self):
        lead = self.env["crm.lead"].create({"name": "Mary's medical", "type": "opportunity",
                                            "user_id": self.agent.id})
        action = lead.action_odin_set_renewal()
        renewal = self.Renewal.with_context(**action["context"]).create({
            "renewal_date": date(2026, 12, 20), "insurer_id": self.insurer_afya.id})
        self.assertEqual(renewal.lead_id, lead)
        self.assertEqual(renewal.origin, "prospect")
        self.assertEqual(lead.odin_renewal_count, 1)
        self.Renewal._cron_remind(today=date(2026, 11, 20))
        self.assertEqual(renewal.lead_id, lead, "the prospect's own lead, not a new one")
        self.assertEqual(lead.date_deadline, date(2026, 12, 20))
        lead.action_set_lost()
        self.assertEqual(renewal.state, "lost")

    def test_renewal_quote_closes_it(self):
        comparison = self.sell()
        renewal = comparison.odin_next_renewal_id
        action = renewal.action_odin_start_quote()
        self.assertEqual(action["params"]["prefill_comparison_id"], comparison.id)
        lead = renewal.lead_id
        params = self.Quote.load_prefill(comparison.id)
        self.assertEqual(params["cover"]["principal_age"], 36, "a year older")
        self.assertNotIn("customer", params)
        saved = self.Quote.save_quote(dict(params, customer={"source": "lead", "lead_id": lead.id}))
        again = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        self.assertEqual(again.odin_renewal_id, renewal)
        accept = again.action_odin_accept()
        self.env["odin.medical.quote.accept"].with_user(self.agent).with_context(
            **accept["context"]).create({"payment": "later",
                                         "cover_start": date(2027, 3, 1)}).action_accept()
        self.assertEqual(renewal.state, "renewed")
        self.assertEqual(renewal.next_renewal_id.renewal_date, date(2028, 3, 1))

    def test_cancelled_order(self):
        comparison = self.sell()
        renewal = comparison.odin_next_renewal_id
        comparison.odin_accepted_order_id._action_cancel()
        self.assertEqual(renewal.state, "cancelled")

    def test_digest(self):
        renewal = self.sell().odin_next_renewal_id
        self.Renewal._cron_digest(today=renewal.renewal_date - relativedelta(days=5))
        mail = self.env["mail.mail"].search([("subject", "like", "Renewals:")], limit=1)
        self.assertIn(self.manager.partner_id, mail.recipient_ids)
        self.assertIn("Henry", mail.body_html)
