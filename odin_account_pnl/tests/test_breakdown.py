"""Unfolding: accounts, "What's in this number", the journal items, the
ledger with its running balance, and the items behind one cell."""

from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged

from ..models import odin_pnl_report

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

    # ------------------------------------------------------------------
    # Documents, periods, salesperson, orders
    # ------------------------------------------------------------------

    def test_a_split_by_document_lists_each_invoice_or_entry(self):
        rows = self.children(f"L:REV/A:{self.revenue.id}", "document")
        self.assertEqual(sorted(row["values"]["c0"] for row in rows), [100.0, 300.0, 600.0])
        move = self.env["account.move.line"].search(
            [("account_id", "=", self.revenue.id), ("balance", "=", -300.0)]).move_id
        row = next(row for row in rows if row["values"]["c0"] == 300.0)
        self.assertTrue(row["name"].startswith(move.name), "named by its reference")
        self.assertIn("partner_b", row["name"], "and who it was with")
        items = self.children(row["key"])
        self.assertEqual([item["move_id"] for item in items], [move.id])

    def test_periods_read_in_order_and_all_of_them(self):
        account = f"L:REV/A:{self.revenue.id}"
        days = self.children(account, "day")
        self.assertEqual([(row["key"].rsplit(":", 1)[1], row["values"]["c0"]) for row in days],
                         [("2026-09-05", 600.0), ("2026-09-06", 300.0), ("2026-09-07", 100.0)],
                         "by date, not largest first")
        self.assertEqual(days[0]["name"], "Sat 5 Sep 2026")
        weeks = self.children(account, "week")
        self.assertEqual([(row["name"], row["values"]["c0"]) for row in weeks],
                         [("Week of 31 Aug 2026", 900.0), ("Week of 7 Sep 2026", 100.0)])
        self.assertEqual([item["values"]["c0"] for item in self.children(weeks[1]["key"])], [100.0])
        quarters = self.children(account, "quarter")
        self.assertEqual([(row["name"], row["values"]["c0"]) for row in quarters], [("Q3 2026", 1000.0)])

    def test_a_long_split_by_day_keeps_a_year_and_puts_the_rest_under_others(self):
        account = f"L:REV/A:{self.revenue.id}"
        with patch.object(odin_pnl_report, "MAX_PERIODS", 2):
            rows = self.children(account, "day")
            self.assertEqual([row["type"] for row in rows], ["breakdown", "breakdown", "others"])
            self.assertEqual(rows[-1]["values"]["c0"], 100.0, "the later days")
            self.assertEqual(len(self.children(account, "day", show_all=True)), 3)

    def test_a_saved_split_this_database_cannot_make_falls_back_to_the_items(self):
        """A view saved with a row split by purchase order, opened where
        Purchase is no longer installed, must not take the report down."""
        key = f"L:REV/A:{self.revenue.id}"
        for mode in ("no_such_split", "purchase_order", "sale_order"):
            if mode != "no_such_split" and self.report._dimension_available(mode):
                continue
            rows = self.report.get_report(self.options(unfolded=["L:REV", key], modes={key: mode}))["rows"]
            items = [row for row in rows if row["parent"] == key]
            self.assertEqual({row["type"] for row in items}, {"entry"}, mode)

    def test_a_partner_splits_again_by_day(self):
        key = f"L:REV/A:{self.revenue.id}/B:partner:{self.partner_a.id}"
        rows = self.children(key, "day")
        self.assertEqual([row["values"]["c0"] for row in rows], [600.0, 100.0])

    def test_entries_without_a_salesperson_say_so(self):
        rows = self.children(f"L:REV/A:{self.revenue.id}", "salesperson")
        self.assertEqual([(row["name"], row["values"]["c0"]) for row in rows], [("No salesperson", 1000.0)])

    def test_the_menu_offers_orders_only_with_their_apps(self):
        menu = self.report.get_report(self.options())["split_menu"]
        modes = [mode for section in menu for mode, _label in section["modes"]]
        self.assertIn("document", modes)
        self.assertIn(f"analytic:{self.plan.id}", modes)
        for dim, field in (("purchase_order", "purchase_line_id"), ("sale_order", "sale_line_ids")):
            self.assertEqual(dim in modes, field in self.env["account.move.line"]._fields)

    def test_a_bill_splits_by_its_purchase_order(self):
        if "purchase_line_id" not in self.env["account.move.line"]._fields:
            self.skipTest("purchase is not installed")
        order = self.env["purchase.order"].create({
            "partner_id": self.partner_a.id,
            "order_line": [Command.create({
                "product_id": self.product_a.id, "product_qty": 2, "price_unit": 50.0, "tax_ids": False})],
        })
        order.button_confirm()
        order.order_line.qty_received = 2
        bill = self.env["account.move"].browse(order.action_create_invoice()["res_id"])
        bill.invoice_date = "2026-09-20"
        bill.action_post()
        account = bill.invoice_line_ids.account_id
        key = f"L:{self._line_of(account)}/A:{account.id}"
        rows = self.children(key, "purchase_order")
        by_name = {row["name"].split(" · ")[0]: row for row in rows}
        self.assertAlmostEqual(by_name[order.name]["values"]["c0"], 100.0)
        items = self.children(by_name[order.name]["key"])
        self.assertEqual([item["move_id"] for item in items], [bill.id])
        action = self.report.action_open_items(self.options(), by_name[order.name]["key"])
        self.assertEqual(self.env["account.move.line"].search(action["domain"]), bill.invoice_line_ids)

    def test_an_invoice_splits_by_its_sales_order(self):
        if "sale_line_ids" not in self.env["account.move.line"]._fields:
            self.skipTest("sale is not installed")
        # Made by a salesperson; split and opened by the accountant, who has
        # no access to sales orders.
        order = self.env["sale.order"].sudo().create({
            "partner_id": self.partner_b.id,
            "order_line": [Command.create({
                "product_id": self.product_a.id, "product_uom_qty": 1, "price_unit": 250.0, "tax_ids": False})],
        })
        order.action_confirm()
        order.order_line.qty_delivered = 1
        invoice = order._create_invoices()
        invoice.invoice_date = "2026-09-21"
        invoice.action_post()
        self.assertFalse(self.env.user.has_group("sales_team.group_sale_salesman"))
        account = invoice.invoice_line_ids.account_id
        rows = self.children(f"L:{self._line_of(account)}/A:{account.id}", "sale_order")
        by_name = {row["name"].split(" · ")[0]: row for row in rows}
        self.assertAlmostEqual(by_name[order.name]["values"]["c0"], 250.0)
        items = self.children(by_name[order.name]["key"])
        self.assertEqual([item["move_id"] for item in items], [invoice.id])

    def _line_of(self, account):
        """The code of the layout line an account is reported on."""
        rows = self.report.get_report(self.options(unfold_all=True))["rows"]
        return next(row["key"].split("/")[0][2:] for row in rows if row.get("account_id") == account.id)
