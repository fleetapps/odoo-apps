# -*- coding: utf-8 -*-
"""The three engines, against real journal items.

Signs are the thing to watch. Sales are credits in Odoo, so a sales tag sums to
a negative balance and the report expression carries a leading minus to turn it
back into the positive figure a return shows. Purchases are debits and carry no
minus. Getting that backwards produces a return that looks right and is exactly
wrong, so every sign is asserted here rather than assumed.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import KeVatCase


@tagged("post_install_l10n", "post_install", "-at_install")
class TestRender(KeVatCase):

    # ------------------------------------------------------------ tax_tags

    def test_a_standard_rated_sale_lands_in_box_1_as_a_positive_figure(self):
        self.post_invoice("ST16", 100_000.0)
        rendered = self.render.render(self.vat3, self.options())

        self.assertEqual(self.box(rendered, "box_1", "base"), 100_000.0)
        self.assertEqual(self.box(rendered, "box_1", "tax"), 16_000.0)

    def test_a_purchase_lands_in_box_7_as_a_positive_figure(self):
        self.post_invoice("PT16", 50_000.0, move_type="in_invoice")
        rendered = self.render.render(self.vat3, self.options())

        self.assertEqual(self.box(rendered, "box_7", "base"), 50_000.0)
        self.assertEqual(self.box(rendered, "box_7", "tax"), 8_000.0)

    def test_a_credit_note_reduces_the_box_it_came_from(self):
        self.post_invoice("ST16", 100_000.0)
        self.post_invoice("ST16", 30_000.0, move_type="out_refund")

        rendered = self.render.render(self.vat3, self.options())

        self.assertEqual(self.box(rendered, "box_1", "base"), 70_000.0)
        self.assertEqual(self.box(rendered, "box_1", "tax"), 11_200.0)

    def test_each_kenyan_rate_reaches_its_own_box(self):
        self.post_invoice("ST16", 100_000.0)
        self.post_invoice("ST8", 200_000.0)
        self.post_invoice("ST0", 300_000.0)
        self.post_invoice("STEX", 400_000.0)

        rendered = self.render.render(self.vat3, self.options())

        self.assertEqual(self.box(rendered, "box_1", "base"), 100_000.0)
        self.assertEqual(self.box(rendered, "box_2", "base"), 200_000.0)
        self.assertEqual(self.box(rendered, "box_3", "base"), 300_000.0)
        self.assertEqual(self.box(rendered, "box_4", "base"), 400_000.0)

    def test_an_invoice_outside_the_period_is_not_counted(self):
        self.post_invoice("ST16", 100_000.0, date="2026-07-31")
        self.post_invoice("ST16", 250_000.0, date="2026-08-15")
        self.post_invoice("ST16", 100_000.0, date="2026-09-01")

        rendered = self.render.render(self.vat3, self.options())

        self.assertEqual(self.box(rendered, "box_1", "base"), 250_000.0)

    def test_a_draft_invoice_is_left_out_unless_asked_for(self):
        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.customer.id,
            "invoice_date": self.period_from,
            "date": self.period_from,
            "company_id": self.ke_company.id,
            "invoice_line_ids": [(0, 0, {
                "name": "Draft",
                "quantity": 1,
                "price_unit": 80_000.0,
                "tax_ids": [(6, 0, self.ke_tax("ST16").ids)],
            })],
        })
        self.assertEqual(move.state, "draft")

        posted_only = self.render.render(self.vat3, self.options())
        self.assertEqual(self.box(posted_only, "box_1", "base"), 0.0)

        including_draft = self.render.render(
            self.vat3, self.options(state="all"))
        self.assertEqual(self.box(including_draft, "box_1", "base"), 80_000.0)

    # --------------------------------------------------------- aggregation

    def test_the_totals_add_up_the_boxes_beneath_them(self):
        self.post_invoice("ST16", 100_000.0)
        self.post_invoice("ST8", 200_000.0)
        self.post_invoice("ST0", 300_000.0)
        self.post_invoice("STEX", 400_000.0)

        rendered = self.render.render(self.vat3, self.options())

        # Box 5 is every sale; box 6 is only the taxable ones, so exempt is out.
        self.assertEqual(self.box(rendered, "box_5", "base"), 1_000_000.0)
        self.assertEqual(self.box(rendered, "box_6", "base"), 600_000.0)
        self.assertEqual(self.box(rendered, "box_5", "tax"), 32_000.0)

    def test_the_period_position_is_output_less_deductible_input(self):
        self.post_invoice("ST16", 1_000_000.0)
        self.post_invoice("PT16", 250_000.0, move_type="in_invoice")

        rendered = self.render.render(self.vat3, self.options())

        self.assertAlmostEqual(self.box(rendered, "box_6"), 160_000.0, places=2)
        self.assertAlmostEqual(self.box(rendered, "box_17"), 40_000.0, places=2)
        self.assertAlmostEqual(self.box(rendered, "box_18"), 120_000.0, places=2)
        self.assertAlmostEqual(self.box(rendered, "box_22"), 120_000.0, places=2)

    def test_an_empty_period_renders_every_box_at_zero(self):
        rendered = self.render.render(self.vat3, self.options())
        self.assertEqual(self.box(rendered, "box_22"), 0.0)
        self.assertEqual(self.box(rendered, "box_26"), 0.0)

    # ------------------------------------------------------------- external

    def test_a_typed_value_reaches_its_box_and_no_other_period(self):
        expression = (
            self.vat3.line_ids.filtered(lambda l: l.code == "box_23")
            .expression_ids.filtered(lambda e: e.label == "tax"))
        self.env["account.report.external.value"].create({
            "name": "VAT paid",
            "value": 12_345.0,
            "date": self.period_to,
            "target_report_expression_id": expression.id,
            "company_id": self.ke_company.id,
        })

        rendered = self.render.render(self.vat3, self.options())
        self.assertEqual(self.box(rendered, "box_23"), 12_345.0)

        next_month = self.render.render(self.vat3, self.options(
            date_from="2026-09-01", date_to="2026-09-30"))
        self.assertEqual(
            self.box(next_month, "box_23"), 0.0,
            "A value typed into August must not reappear in September.")

    # -------------------------------------------------------------- limits

    def test_an_unsupported_engine_is_refused_by_name(self):
        line = self.vat3.line_ids.filtered(lambda l: l.code == "box_1")
        self.env["account.report.expression"].create({
            "report_line_id": line.id,
            "label": "unsupported",
            "engine": "account_codes",
            "formula": "1000",
        })

        with self.assertRaises(UserError) as caught:
            self.render.render(self.vat3, self.options())
        self.assertIn("account_codes", str(caught.exception))

    def test_a_box_opens_the_journal_items_behind_it(self):
        self.post_invoice("ST16", 100_000.0)
        vat_return = self.make_return()
        vat_return.action_compute()

        box_1 = vat_return.line_ids.filtered(lambda l: l.code == "box_1")
        action = box_1.action_drill()

        self.assertEqual(action["res_model"], "account.move.line")
        lines = self.env["account.move.line"].search(action["domain"])
        self.assertTrue(lines)
        self.assertEqual(
            set(lines.mapped("tax_tag_ids.name")),
            {"16% Sales Base", "16% Sales Tax"})

    def test_an_aggregated_box_opens_everything_it_is_built_from(self):
        self.post_invoice("ST16", 100_000.0)
        self.post_invoice("ST8", 200_000.0)
        vat_return = self.make_return()
        vat_return.action_compute()

        box_5 = vat_return.line_ids.filtered(lambda l: l.code == "box_5")
        action = box_5.action_drill()
        lines = self.env["account.move.line"].search(action["domain"])

        self.assertIn("8% Sales Base", lines.mapped("tax_tag_ids.name"))
        self.assertIn("16% Sales Base", lines.mapped("tax_tag_ids.name"))

    def test_a_manual_box_opens_its_stored_values_instead(self):
        vat_return = self.make_return()
        vat_return.action_compute()

        box_23 = vat_return.line_ids.filtered(lambda l: l.code == "box_23")
        action = box_23.action_drill()
        self.assertEqual(
            action["res_model"], "account.report.external.value",
            "Box 23 is typed in, not derived, so there are no journal items "
            "to show.")

    def test_a_period_that_ends_before_it_starts_is_refused(self):
        with self.assertRaises(UserError):
            self.render.render(self.vat3, self.options(
                date_from="2026-08-31", date_to="2026-08-01"))
