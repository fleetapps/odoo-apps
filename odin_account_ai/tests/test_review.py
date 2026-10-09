"""Transaction Review: where suggestions come from, and when accepting one
books it, refuses, or says the item changed."""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import freeze_time, tagged

from .common import AiCase, message


def proposals(*items):
    return message({"suggestions": [
        {"item": index, "account_code": code, "partner": None, "analytic_account": None,
         "confidence": confidence, "reason": "Looks like it."}
        for index, code, confidence in items
    ]})


@freeze_time("2026-10-09")
@tagged("post_install", "-at_install")
class TestReview(AiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bank = cls.company_data["default_journal_bank"]
        cls.purchase = cls.company_data["default_journal_purchase"]
        cls.software = cls.env["account.account"].create(
            {"name": "Software", "code": "630099", "account_type": "expense"})
        cls.vendor = cls.env["res.partner"].create({"name": "Cloudy Ltd"})
        cls.Suggestion = cls.env["odin.ai.suggestion"].with_user(cls.accountant)

    def bank_line(self, amount, label="CARD PAYMENT", partner=None, journal=None, day="2026-09-15"):
        return self.env["account.bank.statement.line"].create({
            "journal_id": (journal or self.bank).id, "date": day, "payment_ref": label,
            "amount": amount, "partner_id": partner and partner.id})

    def bill(self, amount, partner=None, state="posted", account=None):
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": (partner or self.vendor).id, "invoice_date": "2026-09-05",
            "invoice_line_ids": [Command.create({
                "name": "Service", "quantity": 1, "price_unit": amount, "tax_ids": [Command.clear()],
                **({"account_id": account.id} if account else {})})],
        })
        if state == "posted":
            bill.action_post()
        return bill

    def generate(self):
        return self.Suggestion._generate(self.env.company, interactive=True)

    def suggestion_for(self, record):
        field = "st_line_id" if record._name == "account.bank.statement.line" else "aml_id"
        return self.env["odin.ai.suggestion"].search([(field, "=", record.id)])

    # ------------------------------------------------------------------

    def test_an_ai_suggestion_for_a_bank_line_and_booking_it(self):
        line = self.bank_line(-25.0, "UBER TRIP NAIROBI")
        requests = self.mock_api(proposals((0, self.opex.code, 0.95)))
        self.generate()
        suggestion = self.suggestion_for(line)
        self.assertEqual((suggestion.origin, suggestion.proposed_account_id), ("ai", self.opex))
        self.assertEqual(suggestion.confidence, 0.7, "no history agrees: capped")
        self.assertIn('"label": "UBER TRIP NAIROBI"', requests[0]["messages"][0]["content"])
        self.assertIn(self.opex.code, requests[0]["system"][0]["text"])
        self.assertNotIn(self.company_data["default_account_receivable"].code + " ",
                         requests[0]["system"][0]["text"], "only income and expense accounts are offered")
        result = suggestion.with_user(self.accountant).action_accept()
        self.assertEqual(result[0]["state"], "applied", result)
        self.assertTrue(line.is_reconciled)
        liquidity, suspense, other = line._seek_for_lines()
        self.assertFalse(suspense)
        self.assertEqual(other.account_id, self.opex)
        self.assertAlmostEqual(other.balance, 25.0)
        self.assertEqual(line.move_id.state, "posted")
        self.assertIn("approved by", line.move_id.message_ids[0].body)

    def test_the_reviewer_can_pick_another_account_and_make_it_a_rule(self):
        line = self.bank_line(-30.0, "CLOUDY SUBSCRIPTION", partner=self.vendor)
        self.mock_api(proposals((0, self.opex.code, 0.5)))
        self.generate()
        suggestion = self.suggestion_for(line).with_user(self.accountant)
        suggestion.action_accept(account_id=self.software.id, remember=True)
        self.assertEqual(line._seek_for_lines()[2].account_id, self.software)
        rule = self.env["odin.ai.rule"].search([("partner_id", "=", self.vendor.id)])
        self.assertEqual((rule.account_id, rule.direction), (self.software, "out"))
        # Next time the rule answers, without the AI.
        later = self.bank_line(-31.0, "CLOUDY SUBSCRIPTION", partner=self.vendor, day="2026-10-01")
        requests = self.mock_api()
        self.generate()
        self.assertEqual(self.suggestion_for(later).origin, "rule")
        self.assertFalse(requests)

    def test_history_that_agrees_keeps_the_confidence(self):
        self.post("2026-08-01", [(self.software, 40.0, {"partner": self.vendor})])
        agreeing = self.bank_line(-40.0, "CLOUDY", partner=self.vendor)
        self.mock_api(proposals((0, self.software.code, 0.95)))
        self.generate()
        self.assertEqual(self.suggestion_for(agreeing).confidence, 0.95)
        self.assertEqual(self.suggestion_for(agreeing).band, "high")

    def test_paying_an_open_bill_is_reconciled_not_categorised(self):
        bill = self.bill(115.0)
        line = self.bank_line(-115.0, f"PAYMENT {bill.name}", partner=self.vendor)
        requests = self.mock_api()
        self.generate()
        suggestion = self.suggestion_for(line)
        self.assertEqual((suggestion.state, suggestion.match_move_id), ("needs_reconcile", bill))
        self.assertFalse(requests, "the AI is not asked")
        # Nothing to book: it stays for the reconciliation screen.
        self.assertEqual(suggestion.with_user(self.accountant).action_accept()[0]["state"], "needs_reconcile")
        self.assertTrue(line._seek_for_lines()[1], "still on suspense")

    def test_a_partner_with_unpaid_bills_goes_to_reconciliation(self):
        self.bill(500.0)
        line = self.bank_line(-120.0, "CLOUDY PART PAYMENT", partner=self.vendor)
        requests = self.mock_api()
        self.generate()
        suggestion = self.suggestion_for(line)
        self.assertEqual(suggestion.state, "needs_reconcile")
        self.assertIn("unpaid bills", suggestion.rationale)
        self.assertFalse(requests)

    def test_a_transfer_between_own_accounts(self):
        other_bank = self.env["account.journal"].create({"name": "Bank 2", "type": "bank", "code": "BNK9"})
        out = self.bank_line(-300.0, "TO SAVINGS")
        incoming = self.bank_line(300.0, "FROM CURRENT", journal=other_bank, day="2026-09-16")
        self.mock_api()
        self.generate()
        self.assertEqual(self.suggestion_for(out).state, "needs_reconcile")
        self.assertEqual(self.suggestion_for(out).match_move_id, incoming.move_id)
        self.assertEqual(self.suggestion_for(incoming).state, "needs_reconcile")

    def test_an_edited_line_is_not_booked(self):
        line = self.bank_line(-25.0, "TAXI")
        self.mock_api(proposals((0, self.opex.code, 0.8)))
        self.generate()
        line.payment_ref = "TAXI TO AIRPORT"
        suggestion = self.suggestion_for(line).with_user(self.accountant)
        result = suggestion.action_accept()
        self.assertEqual(result[0]["state"], "stale")
        self.assertTrue(line._seek_for_lines()[1], "still on suspense")

    def test_a_locked_period_is_refused_and_the_suggestion_stays(self):
        line = self.bank_line(-25.0, "TAXI")
        self.mock_api(proposals((0, self.opex.code, 0.8)))
        self.generate()
        # Odoo will not set a lock date over unreconciled bank lines; force it
        # to check the guard against lines imported after the lock.
        self.env.company.sudo()._write({"fiscalyear_lock_date": "2026-09-30"})
        self.env.company.invalidate_recordset()
        suggestion = self.suggestion_for(line).with_user(self.accountant)
        result = suggestion.action_accept()
        self.assertEqual(result[0]["state"], "proposed")
        self.assertIn("2026", result[0]["error"])
        self.assertTrue(line._seek_for_lines()[1], "still on suspense")

    def test_a_draft_bill_line_on_the_default_account(self):
        bill = self.bill(80.0, partner=self.env["res.partner"].create({"name": "New Printer"}), state="draft",
                         account=self.purchase.default_account_id)
        self.mock_api(proposals((0, self.software.code, 0.75)))
        self.generate()
        line = bill.invoice_line_ids
        suggestion = self.suggestion_for(line)
        self.assertEqual(suggestion.source, "bill")
        self.assertEqual(suggestion.amount, -80.0)
        suggestion.with_user(self.accountant).action_accept()
        self.assertEqual(line.account_id, self.software)
        self.assertEqual(bill.state, "draft", "the bill is still the accountant's to post")

    def test_a_rejected_item_is_not_suggested_again(self):
        line = self.bank_line(-25.0, "TAXI")
        self.mock_api(proposals((0, self.opex.code, 0.8)))
        self.generate()
        self.suggestion_for(line).with_user(self.accountant).action_reject()
        requests = self.mock_api()
        self.generate()
        self.assertEqual(len(self.suggestion_for(line)), 1)
        self.assertFalse(requests)

    def test_an_unknown_account_gives_no_suggestion(self):
        line = self.bank_line(-25.0, "MYSTERY")
        self.mock_api(proposals((0, self.company_data["default_account_receivable"].code, 0.99)))
        self.generate()
        suggestion = self.suggestion_for(line)
        self.assertFalse(suggestion.proposed_account_id, "only income and expense accounts can be proposed")
        self.assertEqual(suggestion.band, "low")

    def test_when_the_api_fails_items_wait(self):
        line = self.bank_line(-25.0, "TAXI")
        self.mock_api(UserError("The AI is busy right now"))
        self.assertEqual(self.generate(), (0, 0))
        self.assertFalse(self.suggestion_for(line))
        self.assertEqual(len(self.Suggestion._pending_items(self.env.company)), 1)

    def test_only_accountants_book(self):
        line = self.bank_line(-25.0, "TAXI")
        self.mock_api(proposals((0, self.opex.code, 0.8)))
        self.generate()
        with self.assertRaises(AccessError):
            self.suggestion_for(line).with_user(self.billing).action_accept()
        with self.assertRaises(AccessError):
            self.env["odin.ai.suggestion"].with_user(self.billing).inbox()

    def test_the_inbox(self):
        self.bank_line(-25.0, "TAXI")
        self.mock_api(proposals((0, self.opex.code, 0.8)))
        inbox = self.Suggestion.action_generate()
        self.assertEqual(len(inbox["items"]), 1)
        self.assertEqual(inbox["items"][0]["account"]["id"], self.opex.id)
        self.assertTrue(inbox["can_apply"])
        self.assertEqual(inbox["pending"], 0)

    def test_items_handled_elsewhere_leave_the_inbox(self):
        bill = self.bill(115.0)
        line = self.bank_line(-115.0, f"PAYMENT {bill.name}", partner=self.vendor)
        self.mock_api()
        self.generate()
        self.assertEqual(len(self.Suggestion.inbox()["items"]), 1)
        # Reconciled by hand: the suspense line goes to the payable.
        line.with_context(force_delete=True, skip_readonly_check=True).write({"line_ids": [Command.clear()] + [
            Command.create(vals) for vals in line._prepare_move_line_default_vals(
                counterpart_account_id=self.company_data["default_account_payable"].id)]})
        payable = line.move_id.line_ids.filtered(lambda l: l.account_id == self.company_data["default_account_payable"])
        (payable | bill.line_ids.filtered(lambda l: l.account_id == payable.account_id)).reconcile()
        self.assertTrue(line.is_reconciled)
        self.assertFalse(self.Suggestion.inbox()["items"])
        self.assertEqual(self.suggestion_for(line).state, "stale")

    def test_the_nightly_job(self):
        self.bank_line(-25.0, "TAXI")
        self.mock_api(proposals((0, self.opex.code, 0.8)))
        with self.enter_registry_test_mode():
            self.env.ref("odin_account_ai.ir_cron_review_generate").method_direct_trigger()
        self.assertEqual(self.env["odin.ai.suggestion"].search_count([]), 1)
