"""Draft Entry: what blocks an entry before it exists, the entry it makes,
and the reversal it schedules."""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import freeze_time, tagged

from .common import AiCase, message


@freeze_time("2026-10-09")
@tagged("post_install", "-at_install")
class TestDraft(AiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accrued = cls.env["account.account"].create(
            {"name": "Accrued expenses", "code": "214099", "account_type": "liability_current"})
        cls.Draft = cls.env["odin.ai.draft"].with_user(cls.accountant)

    def draft(self, lines, **values):
        return self.Draft.create({
            "name": "Accrual: audit fees September 2026",
            "journal_id": self.misc.id,
            "date": "2026-09-30",
            "line_ids": [Command.create({"account_id": account.id, "name": "Audit", "debit": debit, "credit": credit})
                         for account, debit, credit in lines],
            **values,
        })

    def test_a_balanced_draft_has_no_issue(self):
        draft = self.draft([(self.opex, 1200.0, 0.0), (self.accrued, 0.0, 1200.0)])
        self.assertFalse(draft.issues)

    def test_what_blocks_an_entry(self):
        archived = self.env["account.account"].create(
            {"name": "Old", "code": "699098", "account_type": "expense", "active": False})
        other = self.setup_other_company()
        cases = {
            "not balanced": [(self.opex, 1200.0, 0.0), (self.accrued, 0.0, 1000.0)],
            "is archived": [(archived, 100.0, 0.0), (self.accrued, 0.0, 100.0)],
            "does not belong": [(other["default_account_expense"], 100.0, 0.0), (self.accrued, 0.0, 100.0)],
            "not both": [(self.opex, 100.0, 100.0), (self.accrued, 0.0, 0.0)],
            "at least two lines": [],
        }
        for expected, lines in cases.items():
            with self.subTest(expected):
                draft = self.draft(lines)
                self.assertIn(expected, draft.issues or "")
                with self.assertRaises(UserError):
                    draft.action_create_entry()

    def test_lock_dates_of_both_dates(self):
        self.env.company.sudo().fiscalyear_lock_date = "2026-08-31"
        draft = self.draft([(self.opex, 10.0, 0.0), (self.accrued, 0.0, 10.0)], date="2026-08-15")
        self.assertIn("locked period", draft.issues)
        draft.write({"date": "2026-09-30", "reversal_date": "2026-09-15"})
        self.assertIn("reversal date must be after", draft.issues)

    def test_create_then_post_with_its_reversal(self):
        draft = self.draft([(self.opex, 1200.0, 0.0), (self.accrued, 0.0, 1200.0)], reversal_date="2026-10-01")
        draft.action_create_entry()
        move = draft.move_id
        self.assertEqual((move.state, move.date.isoformat(), move.ref), ("draft", "2026-09-30", draft.name))
        self.assertEqual(sorted(move.line_ids.mapped("balance")), [-1200.0, 1200.0])
        draft.action_post()
        self.assertEqual(move.state, "posted")
        reversal = draft.reversal_move_id
        self.assertEqual(reversal.reversed_entry_id, move)
        self.assertEqual((reversal.state, reversal.auto_post), ("draft", "at_date"))
        self.assertEqual(reversal.date.isoformat(), "2026-10-01")
        self.assertEqual(draft.state, "posted")

    def test_a_possible_duplicate_is_a_warning(self):
        first = self.draft([(self.opex, 1200.0, 0.0), (self.accrued, 0.0, 1200.0)])
        first.action_post()
        second = self.draft([(self.opex, 1200.0, 0.0), (self.accrued, 0.0, 1200.0)])
        self.assertFalse(second.issues)
        self.assertIn("same amount", second.warnings)

    def test_drafting_from_a_sentence(self):
        draft = self.Draft.create({"journal_id": self.misc.id,
                                   "prompt": "Accrue 1,200 audit fees for September, reverse on 1 October."})
        requests = self.mock_api(message({
            "ref": "Accrual: audit fees September 2026", "date": "2026-09-30", "reversal_date": "2026-10-01",
            "lines": [
                {"account_code": self.opex.code, "label": "Audit fees", "debit": 1200, "credit": 0,
                 "partner": "Nobody Known", "analytic_account": "Sales"},
                {"account_code": "XYZ999", "label": "Accrued", "debit": 0, "credit": 1200,
                 "partner": None, "analytic_account": None},
            ],
            "notes": ["No invoice yet."],
        }))
        draft.action_generate()
        self.assertEqual(draft.name, "Accrual: audit fees September 2026")
        self.assertEqual(draft.reversal_date.isoformat(), "2026-10-01")
        first, second = draft.line_ids
        self.assertEqual(first.account_id, self.opex)
        self.assertEqual(first.analytic_distribution, {str(self.sales_dept.id): 100.0})
        self.assertFalse(first.partner_id)
        self.assertIn("Nobody Known", draft.warnings, "an unknown partner is reported, not guessed")
        self.assertFalse(second.account_id)
        self.assertIn("XYZ999", draft.notes)
        self.assertIn("has no account", draft.issues)
        self.assertIn(self.accrued.code, requests[0]["system"][0]["text"])
        self.assertIn("Description (data)", requests[0]["messages"][0]["content"])

    def test_only_accountants(self):
        draft = self.draft([(self.opex, 10.0, 0.0), (self.accrued, 0.0, 10.0)])
        with self.assertRaises(AccessError):
            draft.with_user(self.billing).action_create_entry()
