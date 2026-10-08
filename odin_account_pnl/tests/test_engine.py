"""The figures of the P&L: what each line adds up, the periods, the filters,
and the check that Net Profit equals income minus expenses."""

from odoo.tests import freeze_time, tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestEngine(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-09-10", [
            (cls.revenue, -1000.0),
            (cls.cos, 400.0),
            (cls.opex, 150.0),
            (cls.depreciation, 50.0),
            (cls.other_income, -30.0),
            (cls.other_expense, 10.0),
        ])
        cls.post("2026-08-20", [(cls.revenue, -800.0), (cls.cos, 300.0)])

    def test_net_profit_is_income_minus_expenses(self):
        result = self.run_report()
        expected = {
            "L:REV": 1000.0, "L:COS": 400.0, "L:GP": 600.0, "L:OPEX": 150.0, "L:DEP": 50.0,
            "L:OP": 400.0, "L:OIN": 30.0, "L:OEX": 10.0, "L:NET": 420.0,
        }
        for key, amount in expected.items():
            with self.subTest(line=key):
                self.assertAlmostEqual(self.value(result, key), amount)
        self.assertFalse([w for w in result["warnings"] if w["type"] == "integrity"])

    def test_last_month_is_where_the_report_opens(self):
        with freeze_time("2026-10-08"):
            result = self.report.get_report({})
        self.assertEqual(result["options"]["date"]["date_from"], "2026-09-01")
        self.assertEqual(result["options"]["date"]["date_to"], "2026-09-30")
        self.assertAlmostEqual(self.value(result, "L:NET"), 420.0)

    def test_the_arrows_step_to_the_previous_month(self):
        result = self.run_report(date={"filter": "custom", "date_from": "2026-09-01", "date_to": "2026-09-30", "step": -1})
        self.assertEqual(result["options"]["date"]["date_from"], "2026-08-01")
        self.assertAlmostEqual(self.value(result, "L:REV"), 800.0)

    def test_comparison_shows_growth_and_whether_it_is_good(self):
        result = self.run_report(comparison={"mode": "previous_period", "periods": 1})
        self.assertEqual([c["key"] for c in result["columns"]], ["c0", "c1"])
        revenue = self.row(result, "L:REV")
        self.assertAlmostEqual(revenue["values"]["c1"], 800.0)
        self.assertAlmostEqual(revenue["growth"], 25.0)
        self.assertTrue(revenue["growth_good"])
        cos = self.row(result, "L:COS")
        self.assertAlmostEqual(cos["growth"], 100.0 / 3, places=2)
        self.assertFalse(cos["growth_good"], "costs going up is unfavourable")

    def test_dividing_a_quarter_gives_its_months_and_a_total(self):
        result = self.run_report(
            date={"filter": "custom", "date_from": "2026-07-01", "date_to": "2026-09-30"}, divide="month")
        self.assertEqual([c["group"] for c in result["columns"]], ["current"] * 3 + ["total"])
        revenue = self.row(result, "L:REV")["values"]
        self.assertEqual([revenue[f"c{i}"] for i in range(4)], [0.0, 800.0, 1000.0, 1800.0])

    def test_drafts_count_only_when_asked(self):
        self.post("2026-09-15", [(self.revenue, -500.0)], state="draft")
        self.assertAlmostEqual(self.value(self.run_report(), "L:REV"), 1000.0)
        self.assertAlmostEqual(self.value(self.run_report(include_draft=True), "L:REV"), 1500.0)

    def test_journal_filter(self):
        self.post("2026-09-15", [(self.revenue, -200.0)], journal=self.misc2)
        self.assertAlmostEqual(self.value(self.run_report(), "L:REV"), 1200.0)
        self.assertAlmostEqual(self.value(self.run_report(journal_ids=[self.misc2.id]), "L:REV"), 200.0)

    def test_an_analytic_filter_prorates_a_split_line(self):
        self.post("2026-09-12", [(self.opex, 1000.0, {"analytic": {
            str(self.sales_dept.id): 60.0, str(self.product_dept.id): 40.0}})])
        result = self.run_report(analytic_account_ids=[self.sales_dept.id])
        self.assertAlmostEqual(self.value(result, "L:OPEX"), 600.0)
        # Nothing of the department's is revenue: the line is hidden at zero.
        self.assertIsNone(self.row(result, "L:REV"))
        self.assertAlmostEqual(self.value(result, "L:NET"), -600.0)

    def test_an_archived_account_still_counts(self):
        account = self.env["account.account"].create({"name": "Old fees", "code": "620099", "account_type": "expense"})
        self.post("2026-09-12", [(account, 70.0)])
        account.active = False
        self.assertAlmostEqual(self.value(self.run_report(), "L:OPEX"), 220.0)

    def test_accounts_no_line_covers_are_shown_and_flagged(self):
        layout = self.env.ref("odin_account_pnl.layout_default").copy({"name": "No other income", "is_default": False})
        oin = layout.line_ids.filtered(lambda line: line.code == "OIN")
        oin.account_domain = "[('id', '=', 0)]"
        result = self.run_report(layout_id=layout.id)
        unallocated = self.row(result, "L:_UNALLOCATED")
        self.assertAlmostEqual(unallocated["values"]["c0"], 30.0)
        self.assertTrue([w for w in result["warnings"] if w["type"] == "integrity"])

    def test_an_account_in_two_lines_is_flagged(self):
        layout = self.env.ref("odin_account_pnl.layout_default").copy({"name": "Twice", "is_default": False})
        layout.line_ids.filtered(lambda line: line.code == "OEX").account_domain = \
            "[('account_type', 'in', ('expense_other', 'expense'))]"
        result = self.run_report(layout_id=layout.id)
        self.assertTrue([w for w in result["warnings"] if w["type"] == "overlap"])

    def test_percent_of_revenue(self):
        result = self.run_report(percent_of_base=True)
        self.assertAlmostEqual(self.row(result, "L:GP")["pct"]["c0"], 60.0)

    def test_twelve_month_trend(self):
        result = self.run_report(trend=True)
        trend = self.row(result, "L:REV")["trend"]
        self.assertEqual(len(trend), 12)
        self.assertEqual(trend[-2:], [800.0, 1000.0])
        self.assertEqual(self.row(result, "L:GP")["trend"][-1], 600.0)

    def test_hide_zero_lines(self):
        self.assertIsNone(self.row(self.run_report(
            date={"filter": "custom", "date_from": "2026-01-01", "date_to": "2026-01-31"}), "L:REV"))
        self.assertIsNotNone(self.row(self.run_report(
            date={"filter": "custom", "date_from": "2026-01-01", "date_to": "2026-01-31"}, hide_zero=False), "L:REV"))

    def test_budget_column_and_achievement(self):
        budget = self.env["odin.pnl.budget"].create({
            "name": "Budget", "date_from": "2026-01-01", "date_to": "2026-12-31",
            "line_ids": [
                (0, 0, {"account_id": self.revenue.id, "date": "2026-09-01", "balance": -1250.0}),
                (0, 0, {"account_id": self.cos.id, "date": "2026-09-01", "balance": 500.0}),
            ],
        })
        result = self.run_report(budget_id=budget.id)
        revenue = self.row(result, "L:REV")
        self.assertAlmostEqual(revenue["budget"]["c0"], 1250.0)
        self.assertAlmostEqual(revenue["budget_pct"]["c0"], 80.0)
        self.assertAlmostEqual(self.row(result, "L:GP")["budget"]["c0"], 750.0)
