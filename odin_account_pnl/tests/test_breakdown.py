"""Unfolding: accounts, "What's in this number", the journal items, the
ledger with its running balance, and the items behind one cell."""

from odoo.tests import tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestBreakdown(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-09-05", [(cls.revenue, -600.0, {"partner": cls.partner_a, "product": cls.product_a})])
        cls.post("2026-09-06", [(cls.revenue, -300.0, {"partner": cls.partner_b, "product": cls.product_b})])
        cls.post("2026-09-07", [(cls.revenue, -100.0, {"partner": cls.partner_a})])
        cls.post("2026-09-08", [(cls.opex, 1000.0, {"analytic": {
            str(cls.sales_dept.id): 60.0, str(cls.product_dept.id): 30.0}})])

    def children(self, key, mode=None, **kwargs):
        return self.report.get_children(self.options(), key, mode, **kwargs)["rows"]

    def test_a_line_unfolds_into_its_accounts(self):
        rows = self.children("L:REV")
        self.assertEqual([row["account_id"] for row in rows], [self.revenue.id])
        self.assertAlmostEqual(rows[0]["values"]["c0"], 1000.0)

    def test_a_breakdown_by_partner_adds_up_to_the_account(self):
        rows = self.children(f"L:REV/A:{self.revenue.id}", "partner")
        amounts = {row["name"]: row["values"]["c0"] for row in rows}
        self.assertEqual(amounts, {"partner_a": 700.0, "partner_b": 300.0})
        self.assertAlmostEqual(sum(row["share"] for row in rows), 100.0)
        self.assertEqual(rows[0]["name"], "partner_a", "largest first")

    def test_a_partner_row_unfolds_into_its_items(self):
        key = f"L:REV/A:{self.revenue.id}/B:partner:{self.partner_a.id}"
        rows = self.children(key)
        self.assertEqual(sorted(row["values"]["c0"] for row in rows), [100.0, 600.0])

    def test_breakdowns_by_product_category_and_month(self):
        rows = self.children(f"L:REV/A:{self.revenue.id}", "product_category")
        self.assertAlmostEqual(sum(row["values"]["c0"] for row in rows), 1000.0)
        rows = self.children(f"L:REV/A:{self.revenue.id}", "month")
        self.assertEqual([(row["key"].rsplit(":", 1)[1], row["values"]["c0"]) for row in rows], [("2026-09", 1000.0)])

    def test_an_analytic_plan_split_keeps_the_unassigned_part(self):
        rows = self.children(f"L:OPEX/A:{self.opex.id}", f"analytic:{self.plan.id}")
        amounts = {row["name"]: row["values"]["c0"] for row in rows}
        self.assertAlmostEqual(amounts["Sales"], 600.0)
        self.assertAlmostEqual(amounts["Product"], 300.0)
        self.assertAlmostEqual(amounts["Not assigned"], 100.0)
        unassigned = next(row for row in rows if row["name"] == "Not assigned")
        items = self.children(unassigned["key"])
        self.assertEqual([round(row["values"]["c0"], 2) for row in items], [100.0])

    def test_the_ledger_keeps_a_running_balance(self):
        rows = self.children(f"L:REV/A:{self.revenue.id}", "ledger")
        self.assertEqual([row["running"] for row in rows], [600.0, 900.0, 1000.0])

    def test_a_cell_lists_its_items_largest_first(self):
        rows = self.children(f"L:REV/A:{self.revenue.id}", "audit:c0")
        self.assertEqual([row["values"]["c0"] for row in rows], [600.0, 300.0, 100.0])

    def test_unfolded_rows_come_back_with_the_report(self):
        key = f"L:REV/A:{self.revenue.id}"
        result = self.report.get_report(self.options(unfolded=["L:REV", key], modes={key: "partner"}))
        keys = [row["key"] for row in result["rows"]]
        self.assertIn(f"{key}/B:partner:{self.partner_a.id}", keys)

    def test_the_items_open_in_the_journal_items_list(self):
        action = self.report.action_open_items(self.options(), f"L:REV/A:{self.revenue.id}")
        self.assertEqual(action["res_model"], "account.move.line")
        lines = self.env["account.move.line"].search(action["domain"])
        self.assertAlmostEqual(-sum(lines.mapped("balance")), 1000.0)
