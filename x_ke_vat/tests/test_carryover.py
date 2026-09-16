# -*- coding: utf-8 -*-
"""The credit carried forward, and the fact that it is carried forward once.

Box 26 nets the period. When that comes out as a credit, box 26's
``_carryover_tax`` expression stores it, and next period box 19 reads it back
through the ``most_recent`` external formula and subtracts it from what is owed.

The double-count is the failure worth testing. Nothing in the return shows that
a credit has been carried twice; it just quietly reduces next month's liability
by twice what it should, and the first anyone hears of it is KRA. So closing a
period replaces what that period wrote rather than adding to it, and this file
proves it by closing the same period twice.
"""

from odoo import fields
from odoo.tests import tagged

from .common import KeVatCase


@tagged("post_install_l10n", "post_install", "-at_install")
class TestCarryover(KeVatCase):

    def _august_in_credit(self):
        """A month with purchases and no sales: 100,000 of input VAT."""
        self.post_invoice("PT16", 625_000.0, move_type="in_invoice")
        return self.make_return()

    def test_a_month_in_credit_nets_to_a_negative_box_26(self):
        self._august_in_credit()
        rendered = self.render.render(self.vat3, self.options())

        self.assertAlmostEqual(self.box(rendered, "box_12"), 100_000.0, places=2)
        self.assertAlmostEqual(self.box(rendered, "box_18"), -100_000.0, places=2)
        self.assertAlmostEqual(self.box(rendered, "box_26"), -100_000.0, places=2)

    def test_closing_writes_the_credit_into_the_following_period(self):
        august = self._august_in_credit()
        august.action_close()

        carried = self.env["account.report.external.value"].search([
            ("ke_vat_return_id", "=", august.id),
            ("carryover_origin_expression_label", "!=", False),
        ])
        self.assertEqual(len(carried), 1)
        self.assertAlmostEqual(carried.value, -100_000.0, places=2)
        self.assertEqual(
            carried.date, fields.Date.to_date("2026-08-31"),
            "The credit is dated the last day of the period it comes from. "
            "Box 19 reads most_recent over the PREVIOUS return period, so "
            "September looks for a value dated on or before 31 August; a "
            "value dated 1 September would only surface in October.")

    def test_september_picks_up_augusts_credit(self):
        august = self._august_in_credit()
        august.action_close()

        september = self.render.render(self.vat3, self.options(
            date_from="2026-09-01", date_to="2026-09-30"))

        self.assertAlmostEqual(
            self.box(september, "box_19"), 100_000.0, places=2,
            msg="Box 19 shows the credit brought forward as a positive figure, "
                "because it reduces what is payable.")
        self.assertAlmostEqual(
            self.box(september, "box_22"), -100_000.0, places=2,
            msg="With no September trading, the credit simply rolls on.")

    def test_a_month_in_debit_carries_a_zero_forward(self):
        self.post_invoice("ST16", 1_000_000.0)
        august = self.make_return()
        august.action_close()

        carried = self.env["account.report.external.value"].search([
            ("ke_vat_return_id", "=", august.id),
            ("carryover_origin_expression_label", "!=", False),
        ])
        self.assertEqual(
            len(carried), 1,
            "A period that owes VAT still writes its carryover, at zero. "
            "most_recent has no memory of periods, so a month that wrote "
            "nothing would let the month after it reach back to an older "
            "credit.")
        self.assertEqual(carried.value, 0.0)

        september = self.render.render(self.vat3, self.options(
            date_from="2026-09-01", date_to="2026-09-30"))
        self.assertEqual(
            self.box(september, "box_19"), 0.0,
            "if_below(KES(0)) keeps a liability out of next month's box 19.")

    def test_a_payable_month_stops_an_older_credit_being_applied_twice(self):
        # August in credit, September owing, October must not see August.
        august = self._august_in_credit()
        august.action_close()

        self.post_invoice("ST16", 2_000_000.0, date="2026-09-10")
        september = self.make_return(
            date_from="2026-09-01", date_to="2026-09-30")
        september.action_close()
        self.assertAlmostEqual(
            september.line_ids.filtered(lambda l: l.code == "box_19").tax_value,
            100_000.0, places=2, msg="September consumes August's credit.")
        self.assertGreater(
            september.line_ids.filtered(lambda l: l.code == "box_26").tax_value,
            0.0, "September nets to a payable, so nothing is carried on.")

        october = self.render.render(self.vat3, self.options(
            date_from="2026-10-01", date_to="2026-10-31"))
        self.assertEqual(
            self.box(october, "box_19"), 0.0,
            "August's credit was used in September. October must read "
            "September's zero, not reach back to August.")

    def test_closing_twice_does_not_carry_the_credit_twice(self):
        august = self._august_in_credit()
        august.action_close()
        august.action_reopen()
        august.action_close()

        carried = self.env["account.report.external.value"].search([
            ("ke_vat_return_id", "=", august.id),
            ("carryover_origin_expression_label", "!=", False),
        ])
        self.assertEqual(
            len(carried), 1,
            "Re-closing must replace the carried credit, not add another.")

        september = self.render.render(self.vat3, self.options(
            date_from="2026-09-01", date_to="2026-09-30"))
        self.assertAlmostEqual(
            self.box(september, "box_19"), 100_000.0, places=2,
            msg="Still one month's credit, not two.")

    def test_reopening_removes_the_credit_it_had_carried(self):
        august = self._august_in_credit()
        august.action_close()
        august.action_reopen()

        september = self.render.render(self.vat3, self.options(
            date_from="2026-09-01", date_to="2026-09-30"))
        self.assertEqual(
            self.box(september, "box_19"), 0.0,
            "An unclosed period has not carried anything forward yet.")
