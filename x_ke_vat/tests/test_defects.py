# -*- coding: utf-8 -*-
"""The five l10n_ke defects, pinned so they cannot come back quietly.

Every defect this module corrects produces a plausible number rather than an
error. That is what makes them dangerous and it is what these tests are for: one
test per defect, each asserting both the corrected definition and, where a
figure moves, the figure.

The last test is the important one. Upgrading l10n_ke reloads its data and
reverts our overrides without reloading ours, so the renderer re-checks the
definition on every compute. If that check ever stops working, the module goes
back to producing wrong returns silently, which is exactly where it started.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import KeVatCase

BUGGY_BOX_16 = "box_15.tax - (((box_1.tax + box_2.tax + box_3.base) / box_5.base) * box_15.tax)"
FIXED_BOX_16 = "box_15.tax - ((box_6.base / box_5.base) * box_15.tax)"


@tagged("post_install_l10n", "post_install", "-at_install")
class TestDefects(KeVatCase):

    def _expression(self, code, label):
        line = self.vat3.line_ids.filtered(lambda l, c=code: l.code == c)
        return line.expression_ids.filtered(lambda e, x=label: e.label == x)

    def _set_manual(self, code, label, amount):
        """Type a value into one of the editable external boxes."""
        expression = self._expression(code, label)
        return self.env["account.report.external.value"].create({
            "name": "test",
            "value": amount,
            "date": self.period_to,
            "target_report_expression_id": expression.id,
            "company_id": self.ke_company.id,
        })

    # --------------------------------------------------- 1. box 16 formula

    def test_box_16_apportions_by_turnover_not_by_vat_charged(self):
        self.assertEqual(
            self._expression("box_16", "tax").formula.strip(), FIXED_BOX_16,
            "Box 16 must apportion mixed input VAT by the share of turnover "
            "that is taxable, not by VAT charged over turnover.")

    def test_a_wholly_taxable_trader_has_no_non_deductible_input_vat(self):
        # All sales standard-rated, so every shilling of mixed input VAT is
        # deductible and box 16 is zero. Under the shipped formula the ratio
        # collapses to the VAT rate and box 16 would be 42,000 of the 50,000.
        self.post_invoice("ST16", 1_000_000.0)
        self.post_invoice("PT16", 312_500.0, move_type="in_invoice")
        self._set_manual("box_15", "tax", 50_000.0)

        rendered = self.render.render(self.vat3, self.options())

        self.assertEqual(self.box(rendered, "box_1", "base"), 1_000_000.0)
        self.assertEqual(self.box(rendered, "box_6", "base"), 1_000_000.0)
        self.assertAlmostEqual(self.box(rendered, "box_12"), 50_000.0, places=2)
        self.assertAlmostEqual(
            self.box(rendered, "box_16"), 0.0, places=2,
            msg="Every supply is taxable, so none of the mixed input VAT is "
                "non-deductible. The shipped formula would report 42,000.")
        self.assertAlmostEqual(
            self.box(rendered, "box_17"), 50_000.0, places=2,
            msg="Box 17 is box 12 plus imports, less boxes 14 and 16. With "
                "box 16 correctly zero, the whole input pool is deductible.")

    def test_half_exempt_turnover_halves_the_deductible_input_vat(self):
        self.post_invoice("ST16", 500_000.0)
        self.post_invoice("STEX", 500_000.0)
        self._set_manual("box_15", "tax", 40_000.0)

        rendered = self.render.render(self.vat3, self.options())

        # Taxable turnover 500k of 1m total, so half the pool is deductible.
        self.assertAlmostEqual(self.box(rendered, "box_5", "base"), 1_000_000.0, places=2)
        self.assertAlmostEqual(self.box(rendered, "box_6", "base"), 500_000.0, places=2)
        self.assertAlmostEqual(self.box(rendered, "box_16"), 20_000.0, places=2)

    def test_box_16_survives_a_period_with_no_sales(self):
        # box_5.base is zero, so the ratio divides by zero. The subformula says
        # to ignore that rather than fail the whole return.
        self._set_manual("box_15", "tax", 10_000.0)
        rendered = self.render.render(self.vat3, self.options())
        self.assertEqual(self.box(rendered, "box_16"), 0.0)

    # -------------------------------------------------- 2. export tagging

    def test_the_export_tax_carries_the_zero_rated_tag(self):
        export_tax = self.ke_tax("ST0EXPORT")
        base_lines = export_tax.repartition_line_ids.filtered(
            lambda line: line.repartition_type == "base")
        tag_names = set(base_lines.tag_ids.mapped("name"))

        self.assertIn("Zero Rated Sales Base", tag_names)
        self.assertNotIn(
            "Exempt Sales Base", tag_names,
            "Exports are zero-rated, not exempt. The post-install hook should "
            "have repointed this tax.")

    def test_an_export_sale_reports_as_zero_rated_not_exempt(self):
        self.post_invoice("ST0EXPORT", 250_000.0)

        rendered = self.render.render(self.vat3, self.options())

        self.assertEqual(
            self.box(rendered, "box_3", "base"), 250_000.0,
            "Exports belong in box 3, Sales (Zero Rated).")
        self.assertEqual(
            self.box(rendered, "box_4", "base"), 0.0,
            "Nothing exempt was sold, so box 4 must be empty.")

    def test_exports_count_toward_deductible_input_vat(self):
        # The compounding case: an exporter mis-tagged as exempt loses the
        # export turnover from the box 16 numerator while keeping it in the
        # denominator, so more input VAT is treated as non-deductible.
        self.post_invoice("ST0EXPORT", 1_000_000.0)
        self._set_manual("box_15", "tax", 80_000.0)

        rendered = self.render.render(self.vat3, self.options())

        self.assertAlmostEqual(
            self.box(rendered, "box_16"), 0.0, places=2,
            msg="Zero-rated turnover is taxable turnover, so the whole pool "
                "stays deductible.")

    # ------------------------------------------------ 3. carryover currency

    def test_the_carryover_threshold_is_denominated_in_shillings(self):
        expression = self._expression("box_26", "_carryover_tax")
        self.assertEqual(
            expression.subformula.strip(), "if_below(KES(0))",
            "l10n_ke shipped this threshold in Romanian leu.")

    # ------------------------------------------------------ 4. box 20 base

    def test_box_20_has_no_base_expression(self):
        self.assertFalse(
            self.env.ref(
                "l10n_ke.tax_report_total_withholding_vat_credit_base_tag",
                raise_if_not_found=False),
            "Box 20's base column read the purchase-side tag. With no "
            "sales-side base tag to point at, the expression is removed so the "
            "column renders empty instead of wrong.")

    def test_box_20_still_reports_the_withholding_credit(self):
        rendered = self.render.render(self.vat3, self.options())
        line = next(l for l in rendered["lines"] if l["code"] == "box_20")
        self.assertIsNone(
            line["values"].get("base"),
            "The base column is blank, not zero.")
        self.assertIsNotNone(
            line["values"].get("tax"),
            "The tax column is the figure that feeds box 22 and must survive.")

    # ------------------------------------------ 5. the reversion tripwire

    def test_a_reverted_formula_stops_the_return_rather_than_skewing_it(self):
        self._expression("box_16", "tax").formula = BUGGY_BOX_16

        with self.assertRaises(UserError) as caught:
            self.render.render(self.vat3, self.options())

        message = str(caught.exception)
        self.assertIn(
            "Non-Deductible", message,
            "The error has to name the box that reverted.")
        self.assertIn(
            FIXED_BOX_16, message,
            "The error has to name the corrected formula, or whoever reads it "
            "cannot act on it.")

    def test_a_reverted_export_tax_is_caught_too(self):
        # post_init_hook repoints the tax, and hooks run only on install. An
        # l10n_ke upgrade afterwards can put the exempt tag back, and nothing
        # else in the system would mention it.
        export_tax = self.ke_tax("ST0EXPORT")
        exempt = self.ke_tag("Exempt Sales Base")
        zero_rated = self.ke_tag("Zero Rated Sales Base")
        export_tax.repartition_line_ids.filtered(
            lambda line: line.repartition_type == "base"
        ).write({"tag_ids": [(3, zero_rated.id), (4, exempt.id)]})

        with self.assertRaises(UserError) as caught:
            self.render.render(self.vat3, self.options())
        message = str(caught.exception)
        self.assertIn(self.ke_company.display_name, message)
        self.assertIn("Zero Rated Sales Base", message)

    def test_reloading_the_chart_reapplies_the_export_correction(self):
        # A chart reload -- what an l10n_ke upgrade performs -- rewrites the
        # tags of every unchanged tax from the template
        # (account.chart.template._pre_reload_data keeps only tag_ids), which
        # puts the exempt tag back on ST0EXPORT. x_ke_base hooks the same
        # load's _post_load_data, so the correction must survive the round
        # trip with nobody having to call anything.
        export_tax = self.ke_tax("ST0EXPORT")
        exempt = self.ke_tag("Exempt Sales Base")
        zero_rated = self.ke_tag("Zero Rated Sales Base")
        export_tax.repartition_line_ids.filtered(
            lambda line: line.repartition_type == "base"
        ).write({"tag_ids": [(3, zero_rated.id), (4, exempt.id)]})

        self.env["account.chart.template"].try_loading(
            "ke", company=self.ke_company, install_demo=False)

        base_lines = self.ke_tax("ST0EXPORT").repartition_line_ids.filtered(
            lambda line: line.repartition_type == "base")
        names = set(base_lines.tag_ids.mapped("name"))
        self.assertIn("Zero Rated Sales Base", names)
        self.assertNotIn(
            "Exempt Sales Base", names,
            "The chart-template hook did not re-apply the export correction "
            "after a reload.")
        # And the return computes again without anyone running a repair.
        self.render.render(self.vat3, self.options())

    def test_an_unknown_subformula_is_refused_rather_than_ignored(self):
        self._expression("box_16", "tax").subformula = "round_to_nearest(100)"

        with self.assertRaises(UserError) as caught:
            self.render.render(self.vat3, self.options())
        self.assertIn("round_to_nearest(100)", str(caught.exception))

    def test_a_reinstated_box_20_base_is_also_caught(self):
        line = self.vat3.line_ids.filtered(lambda l: l.code == "box_20")
        self.env["account.report.expression"].create({
            "report_line_id": line.id,
            "label": "base",
            "engine": "tax_tags",
            "formula": "WH base",
        })

        with self.assertRaises(UserError):
            self.render.render(self.vat3, self.options())
