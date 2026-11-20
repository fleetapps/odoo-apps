"""Flux analysis: the change against the comparison in amount, whether it is
material, the headline figures above the table, and each line's latest note
as its explanation."""

import io

import openpyxl

from odoo.tests import tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestFlux(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # August, then September: revenue up 1,000 -> 10,000; costs 400 -> 420.
        cls.post("2026-08-10", [(cls.revenue, -1000.0), (cls.cos, 400.0), (cls.opex, 300.0)])
        cls.post("2026-09-10", [(cls.revenue, -10000.0), (cls.cos, 420.0), (cls.opex, 900.0)])

    def compared(self, **values):
        return self.run_report(comparison={"mode": "previous_period", "periods": 1}, **values)

    def test_the_variance_is_the_change_against_the_comparison(self):
        result = self.compared()
        revenue = self.row(result, "L:REV")
        self.assertAlmostEqual(revenue["variance"], 9000.0)
        self.assertTrue(revenue["growth_good"], "more revenue is good")
        costs = self.row(result, "L:COS")
        self.assertAlmostEqual(costs["variance"], 20.0)
        self.assertFalse(costs["growth_good"], "more costs are not")

    def test_no_comparison_no_variance(self):
        result = self.run_report()
        self.assertIsNone(self.row(result, "L:REV")["variance"])
        self.assertFalse(self.row(result, "L:REV")["material"])

    def test_material_needs_the_percent_and_the_amount(self):
        # Auto amount: 1% of September's revenue, 100.
        result = self.compared()
        self.assertEqual(result["materiality"], {"percent": 10.0, "amount": 100.0, "auto": True})
        self.assertTrue(self.row(result, "L:REV")["material"], "+900%, +9,000")
        self.assertFalse(self.row(result, "L:COS")["material"], "+5%, +20: neither")
        self.assertTrue(self.row(result, "L:OPEX")["material"], "+200%, +600")
        # A typed amount replaces the automatic one.
        result = self.compared(materiality={"percent": 10, "amount": 1000})
        self.assertEqual(result["materiality"], {"percent": 10.0, "amount": 1000.0, "auto": False})
        self.assertFalse(self.row(result, "L:OPEX")["material"], "600 is under 1,000")
        # The percent alone is not enough either way.
        result = self.compared(materiality={"percent": 300, "amount": 0})
        self.assertFalse(self.row(result, "L:OPEX")["material"], "+200% is under 300%")

    def test_a_figure_from_nothing_is_material_by_its_amount(self):
        self.post("2026-09-12", [(self.other_income, -500.0)])
        result = self.compared()
        row = self.row(result, "L:OIN")
        self.assertIsNone(row["growth"], "no percentage from nothing")
        self.assertAlmostEqual(row["variance"], 500.0)
        self.assertTrue(row["material"])
        self.assertTrue(row["growth_good"], "new income is good")

    def test_options_reject_nonsense_thresholds(self):
        options = self.report._normalize_options({"materiality": {"percent": "x", "amount": float("nan")}})
        self.assertEqual(options["materiality"], {"percent": 10.0, "amount": None})
        options = self.report._normalize_options({"materiality": {"percent": -5, "amount": -3}})
        self.assertEqual(options["materiality"], {"percent": 0.0, "amount": 0.0})

    def test_accounts_carry_variance_and_materiality_too(self):
        result = self.compared(detail="accounts")
        account = self.row(result, f"L:OPEX/A:{self.opex.id}")
        self.assertAlmostEqual(account["variance"], 600.0)
        self.assertTrue(account["material"])

    def test_headline_figures_follow_the_layout(self):
        result = self.compared()
        kpis = {kpi["code"]: kpi for kpi in result["kpis"]}
        self.assertEqual(list(kpis), ["REV", "GP", "OP", "NET"])
        self.assertAlmostEqual(kpis["REV"]["value"], 10000.0)
        self.assertAlmostEqual(kpis["REV"]["previous"], 1000.0)
        self.assertAlmostEqual(kpis["REV"]["growth"], 900.0)
        self.assertIsNone(kpis["REV"]["margin"], "the base has no margin on itself")
        self.assertAlmostEqual(kpis["GP"]["margin"], 95.8)
        self.assertAlmostEqual(kpis["NET"]["value"], 8680.0)
        self.assertTrue(kpis["NET"]["growth_good"])
        self.assertEqual(kpis["NET"]["previous_label"], "Aug 2026")

    def test_headline_figures_read_the_total_of_a_divided_period(self):
        result = self.run_report(
            date={"filter": "custom", "date_from": "2026-08-01", "date_to": "2026-09-30"}, divide="month")
        revenue = next(kpi for kpi in result["kpis"] if kpi["code"] == "REV")
        self.assertAlmostEqual(revenue["value"], 11000.0)
        self.assertEqual(revenue["column_label"], "Total")

    def test_the_latest_note_explains_the_line(self):
        self.report.add_annotation(self.options(), "L:REV", "A new client in September")
        self.report.add_annotation(self.options(), "L:REV", "Mostly the Acme contract")
        row = self.row(self.compared(), "L:REV")
        self.assertEqual(row["annotations"], 2)
        self.assertEqual(row["note"]["text"], "Mostly the Acme contract")
        self.assertIsNone(self.row(self.compared(), "L:COS")["note"])

    def test_the_exports_carry_the_variance(self):
        options = self.options(comparison={"mode": "previous_period", "periods": 1})
        payload = self.env["odin.pnl.export"]._payload(options)
        labels = [column["label"] for column in payload["columns"]]
        self.assertEqual(labels[-2:], ["Variance", "%"])
        revenue = next(row for row in payload["rows"] if row["key"] == "L:REV")
        self.assertEqual(revenue["cells"][-2]["text"], "+9,000.00")
        self.assertEqual(revenue["cells"][-2]["value"], 9000.0)
        self.assertEqual([kpi["code"] for kpi in payload["kpis"]], ["REV", "GP", "OP", "NET"])
        self.assertEqual(payload["kpis"][0]["growth_text"], "+900.0%")
        content, _name, _type = self.env["odin.pnl.export"].render(options, "xlsx")
        sheet = openpyxl.load_workbook(io.BytesIO(content)).active
        header = [cell.value for cell in next(sheet.iter_rows(min_row=5, max_row=5))]
        self.assertIn("Variance", header)
