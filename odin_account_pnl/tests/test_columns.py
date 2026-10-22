"""What accountants print beside the period: the year to date, each company
of a group side by side, the budget variance; and what the year-end review
asks of the figures: drafts left out, the profit's appropriation below it."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestColumns(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-03-10", [(cls.revenue, -400.0)])
        cls.post("2026-09-10", [(cls.revenue, -1000.0)])
        cls.other = cls.setup_other_company()
        other_company = cls.other["company"]
        clearing = cls.env["account.account"].with_company(other_company).create({
            "name": "Clearing", "code": "199099", "account_type": "asset_current",
            "company_ids": [other_company.id]})
        cls.env["account.move"].with_company(other_company).create({
            "move_type": "entry", "date": "2026-09-12", "journal_id": cls.other["default_journal_misc"].id,
            "line_ids": [
                (0, 0, {"account_id": cls.other["default_account_revenue"].id, "credit": 5000.0}),
                (0, 0, {"account_id": clearing.id, "debit": 5000.0}),
            ],
        }).action_post()
        # setup_other_company() leaves both companies active: the report looks
        # at the first one unless a test selects both.
        cls.report = cls.report.with_context(allowed_company_ids=[cls.env.company.id])

    def both_companies(self):
        return self.report.with_context(allowed_company_ids=[self.env.company.id, self.other["company"].id])

    def run_report(self, user=None, **values):
        return self.report.get_report(self.options(**values))

    # ---- year to date ---------------------------------------------------

    def test_a_year_to_date_column_runs_from_the_fiscal_year_start(self):
        result = self.run_report(ytd=True)
        columns = result["columns"]
        self.assertEqual([(column["group"], column["date_from"]) for column in columns],
                         [("current", "2026-09-01"), ("ytd", "2026-01-01")])
        self.assertAlmostEqual(self.value(result, "L:REV", "c1"), 1400.0)

    def test_a_period_that_starts_the_year_needs_no_year_to_date(self):
        result = self.run_report(ytd=True, date={"filter": "custom", "date_from": "2026-01-01", "date_to": "2026-12-31"})
        self.assertEqual([column["group"] for column in result["columns"]], ["current"])

    def test_the_budget_is_also_shown_to_date(self):
        budget = self.env["odin.pnl.budget"].create({
            "name": "Budget", "date_from": "2026-01-01", "date_to": "2026-12-31",
            "line_ids": [
                (0, 0, {"account_id": self.revenue.id, "date": "2026-03-01", "balance": -500.0}),
                (0, 0, {"account_id": self.revenue.id, "date": "2026-09-01", "balance": -1250.0}),
            ],
        })
        revenue = self.row(self.run_report(ytd=True, budget_id=budget.id), "L:REV")
        self.assertAlmostEqual(revenue["budget"]["c1"], 1750.0)
        self.assertAlmostEqual(revenue["budget_var"]["c0"], -250.0, msg="1,000 against 1,250: 250 short")
        self.assertAlmostEqual(revenue["budget_var"]["c1"], -350.0)

    def test_a_year_to_date_budget_is_not_typed(self):
        budget = self.env["odin.pnl.budget"].create({
            "name": "Budget", "date_from": "2026-01-01", "date_to": "2026-12-31"})
        options = self.options(ytd=True, budget_id=budget.id)
        account = next(row for row in self.report.get_children(options, "L:REV")["rows"]
                       if row["type"] == "account")
        with self.assertRaises(UserError):
            self.report.set_budget_amount(options, account["key"], "c1", 1200.0)
        self.assertFalse(budget.line_ids, "no month was overwritten")

    def test_a_divided_period_has_no_year_to_date(self):
        result = self.run_report(ytd=True, divide="month", date={
            "filter": "custom", "date_from": "2026-08-01", "date_to": "2026-09-30"})
        self.assertFalse(result["options"]["ytd"])
        self.assertNotIn("ytd", [column["group"] for column in result["columns"]])

    # ---- companies side by side ------------------------------------------

    def test_companies_side_by_side_add_up_to_their_total(self):
        result = self.both_companies().get_report(self.options(by_company=True))
        columns = result["columns"]
        self.assertEqual([column["group"] for column in columns], ["company", "company", "total"])
        by_company = {column["company_id"]: column["key"] for column in columns if column["company_id"]}
        revenue = self.row(result, "L:REV")["values"]
        self.assertAlmostEqual(revenue[by_company[self.env.company.id]], 1000.0)
        self.assertAlmostEqual(revenue[by_company[self.other["company"].id]], 5000.0)
        self.assertAlmostEqual(revenue[columns[-1]["key"]], 6000.0)
        self.assertEqual(result["options"]["comparison"]["mode"], "none", "the companies are the columns")

    def test_a_company_column_lists_only_that_companys_items(self):
        report = self.both_companies()
        result = report.get_report(self.options(by_company=True, detail="accounts"))
        other_column = next(column["key"] for column in result["columns"]
                            if column["company_id"] == self.other["company"].id)
        account = next(row for row in result["rows"]
                       if row["type"] == "account" and row["values"].get(other_column))
        items = report.get_children(self.options(by_company=True), account["key"], f"audit:{other_column}")["rows"]
        self.assertEqual([item["values"][other_column] for item in items], [5000.0])

    def test_one_company_has_no_side_by_side(self):
        result = self.run_report(by_company=True)
        self.assertFalse(result["options"]["by_company"])
        self.assertEqual(len(result["columns"]), 1)

    def test_a_budget_shows_in_its_companys_column_only(self):
        budget = self.env["odin.pnl.budget"].create({
            "name": "Budget", "date_from": "2026-01-01", "date_to": "2026-12-31",
            "line_ids": [(0, 0, {"account_id": self.revenue.id, "date": "2026-09-01", "balance": -1250.0})],
        })
        result = self.both_companies().get_report(self.options(by_company=True, budget_id=budget.id))
        own_column = next(column["key"] for column in result["columns"]
                          if column["company_id"] == self.env.company.id)
        self.assertEqual(list(self.row(result, "L:REV")["budget"]), [own_column],
                         "never against the other company or the group total")

    def test_a_scheduled_view_of_companies_side_by_side_sends_them(self):
        self.accountant.company_ids |= self.other["company"]
        companies = [self.env.company.id, self.other["company"].id]
        report = self.report.with_user(self.accountant).with_context(allowed_company_ids=companies)
        saved = report.save_view(self.options(by_company=True), "Group")
        view = self.env["odin.pnl.view"].browse(saved["id"])
        self.assertEqual(set(view.company_ids.ids), set(companies))
        view.write({"schedule": "daily", "recipient_emails": "boss@example.com"})
        Export = type(self.env["odin.pnl.export"])
        payload = Export._payload
        sent = []

        def spy(export, options):
            result = payload(export, options)
            sent.append((result["options"]["by_company"], tuple(export.env.companies.ids)))
            return result
        with patch.object(Export, "_payload", spy), \
                patch.object(type(self.env["mail.mail"]), "send", lambda mails, **kw: mails.write({"state": "sent"})):
            view.sudo()._send_and_record()
        self.assertTrue(sent, "the email was rendered")
        self.assertEqual(set(sent), {(True, tuple(companies))}, "the body and the PDF, both companies")

    # ---- the year-end review ---------------------------------------------

    def test_drafts_left_out_are_counted_and_listed(self):
        self.post("2026-09-20", [(self.revenue, -70.0)], state="draft")
        result = self.run_report()
        self.assertEqual(result["draft_count"], 1)
        action = self.report.action_open_drafts(self.options())
        drafts = self.env["account.move.line"].search(action["domain"])
        self.assertEqual(drafts.mapped("balance"), [-70.0])
        self.assertEqual(self.run_report(include_draft=True)["draft_count"], 0, "counted, so nothing to say")

    def test_the_appropriation_shows_below_the_profit_without_changing_it(self):
        appropriation = self.env["account.account"].create({
            "name": "Appropriation to reserves", "code": "692099", "account_type": "equity_unaffected"})
        reserve = self.env["account.account"].create({
            "name": "Legal reserve", "code": "130099", "account_type": "equity"})
        self.post("2026-09-30", [(appropriation, 300.0), (reserve, -300.0)])
        result = self.run_report()
        self.assertAlmostEqual(self.value(result, "L:NET"), 1000.0, msg="the profit is the profit")
        self.assertAlmostEqual(self.value(result, "L:APP"), 300.0)
        self.assertFalse([warning for warning in result["warnings"] if warning["type"] == "integrity"])
        self.assertNotIn("L:_UNALLOCATED", [row["key"] for row in result["rows"]])

    def test_a_catch_all_line_does_not_start_counting_the_appropriation(self):
        appropriation = self.env["account.account"].create({
            "name": "Appropriation to reserves", "code": "692099", "account_type": "equity_unaffected"})
        layout = self.env["odin.pnl.layout"].create({"name": "Everything", "line_ids": [
            (0, 0, {"code": "ALL", "name": "All", "kind": "accounts", "account_domain": "[]", "sign": "credit"}),
        ]})
        result = self.run_report(layout_id=layout.id)
        accounts = [row["account_id"] for row in self.report.get_children(
            self.options(layout_id=layout.id), "L:ALL")["rows"]]
        self.assertNotIn(appropriation.id, accounts)
        self.assertAlmostEqual(self.value(result, "L:ALL"), 1000.0)
