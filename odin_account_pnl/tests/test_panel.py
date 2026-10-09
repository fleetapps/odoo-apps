"""What the side panel and the cells change: the entry peek, notes, and
budgets typed into the report."""

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestPanel(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.move = cls.post("2026-09-10", [(cls.revenue, -1000.0, {"partner": cls.partner_a, "label": "Consulting"})])
        cls.readonly = new_test_user(
            cls.env, login="pnl_auditor",
            groups="base.group_user,account.group_account_readonly",
            company_id=cls.env.company.id, company_ids=[cls.env.company.id])

    def test_the_peek_shows_the_document_and_marks_the_line(self):
        line = self.move.line_ids.filtered(lambda l: l.account_id == self.revenue)
        peek = self.report.get_entry_peek(line.id)
        self.assertEqual(peek["move_id"], self.move.id)
        current = [item for item in peek["lines"] if item["current"]]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["credit"], 1000.0)

    def test_notes_go_on_lines_and_accounts_and_only_accountants_write_them(self):
        options = self.options()
        notes = self.report.with_user(self.accountant).add_annotation(options, "L:REV", "Big month")
        self.assertEqual([note["note"] for note in notes], ["Big month"])
        result = self.report.get_report(options)
        self.assertEqual(self.row(result, "L:REV")["annotations"], 1)
        with self.assertRaises(AccessError):
            self.report.with_user(self.readonly).add_annotation(options, "L:REV", "Not allowed")
        self.assertEqual(len(self.report.with_user(self.readonly).get_annotations(options, "L:REV")), 1)

    def test_a_budget_typed_in_a_quarter_is_spread_over_its_months(self):
        budget_id = self.report.with_user(self.accountant).create_budget(self.options(), "Plan")
        options = self.options(
            date={"filter": "custom", "date_from": "2026-07-01", "date_to": "2026-09-30"}, budget_id=budget_id)
        key = f"L:REV/A:{self.revenue.id}"
        self.report.with_user(self.accountant).set_budget_amount(options, key, "c0", 3000.01)
        lines = self.env["odin.pnl.budget.line"].search([("budget_id", "=", budget_id)], order="date")
        # Income is stored signed like a balance: credit, negative.
        self.assertEqual(lines.mapped("balance"), [-1000.0, -1000.0, -1000.01])
        result = self.report.get_report(options)
        self.assertAlmostEqual(self.row(result, "L:REV")["budget"]["c0"], 3000.01)
        self.assertAlmostEqual(self.row(result, "L:REV")["budget_pct"]["c0"], 1000.0 / 3000.01 * 100)

    def test_a_percentage_against_a_budget_of_the_other_sign_is_left_empty(self):
        budget_id = self.report.create_budget(self.options(), "Costs only")
        options = self.options(budget_id=budget_id)
        self.post("2026-09-11", [(self.opex, 400.0)])
        self.report.set_budget_amount(options, f"L:OPEX/A:{self.opex.id}", "c0", 500.0)
        result = self.report.get_report(options)
        self.assertAlmostEqual(self.row(result, "L:OPEX")["budget_pct"]["c0"], 80.0)
        self.assertIsNone(self.row(result, "L:NET")["budget_pct"]["c0"])

    def test_readonly_users_cannot_type_budgets(self):
        budget_id = self.report.create_budget(self.options(), "Plan")
        with self.assertRaises(AccessError):
            self.report.with_user(self.readonly).set_budget_amount(
                self.options(budget_id=budget_id), f"L:REV/A:{self.revenue.id}", "c0", 10.0)
