# -*- coding: utf-8 -*-
"""Closing, locking, and refusing to quietly unlock.

The lock is the whole point of closing. Once a period has been declared to KRA,
a journal item posted into it makes the ledger disagree with the filed return,
and nothing in Odoo would tell you. Community already refuses that through the
tax lock date; this module's job is to set it at the right moment and never to
lower it behind anyone's back.
"""

from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import KeVatCase


@tagged("post_install_l10n", "post_install", "-at_install")
class TestLifecycle(KeVatCase):

    def test_computing_freezes_one_line_per_box(self):
        self.post_invoice("ST16", 100_000.0)
        vat_return = self.make_return()
        vat_return.action_compute()

        box_1 = vat_return.line_ids.filtered(lambda l: l.code == "box_1")
        self.assertEqual(len(box_1), 1)
        self.assertEqual(box_1.base_value, 100_000.0)
        self.assertEqual(box_1.tax_value, 16_000.0)
        self.assertTrue(box_1.has_base and box_1.has_tax)

    def test_a_box_with_no_figure_reads_as_blank_not_as_zero(self):
        vat_return = self.make_return()
        vat_return.action_compute()

        box_3 = vat_return.line_ids.filtered(lambda l: l.code == "box_3")
        self.assertTrue(box_3.has_base)
        self.assertFalse(
            box_3.has_tax,
            "Zero-rated sales carry no VAT, so box 3 has no tax figure at all.")
        self.assertEqual(box_3.tax_display, "")

    def test_the_figures_stop_moving_once_the_period_is_closed(self):
        self.post_invoice("ST16", 100_000.0)
        vat_return = self.make_return()
        vat_return.action_close()

        self.assertEqual(vat_return.state, "closed")
        with self.assertRaises(UserError):
            vat_return.action_compute()

    def test_closing_sets_the_tax_lock_date(self):
        vat_return = self.make_return()
        self.assertFalse(self.ke_company.tax_lock_date)

        vat_return.action_close()

        self.assertEqual(self.ke_company.tax_lock_date, self.period_to)

    def test_closing_never_pulls_the_lock_backwards(self):
        self.ke_company.sudo().tax_lock_date = "2026-12-31"
        self.make_return().action_close()

        self.assertEqual(
            str(self.ke_company.tax_lock_date), "2026-12-31",
            "A company already locked further forward is left alone.")

    def test_a_taxed_invoice_posted_after_closing_moves_to_the_next_period(self):
        # Odoo does not refuse a new entry dated inside a locked period: it
        # moves the accounting date to the first open day after the lock, so
        # the invoice reports in September's return rather than changing
        # August's (account.move._post; the official docs say its tax values
        # are "moved to the next open tax period").
        self.make_return().action_close()

        move = self.post_invoice("ST16", 50_000.0, date="2026-08-20")

        self.assertEqual(move.state, "posted")
        self.assertGreater(move.date, self.period_to)
        august = self.render.render(self.vat3, self.options())
        self.assertEqual(
            self.box(august, "box_1", "base"), 0.0,
            "The declared period did not move.")

    def test_editing_a_taxed_entry_inside_a_closed_period_is_refused(self):
        move = self.post_invoice("ST16", 50_000.0, date="2026-08-20")
        self.make_return().action_close()

        base_line = move.line_ids.filtered("tax_ids")
        with self.assertRaises(UserError):
            base_line.write({"tax_tag_ids": [(5, 0, 0)]})

    def test_periods_close_in_order(self):
        self.make_return().action_close()  # August
        october = self.make_return(date_from="2026-10-01", date_to="2026-10-31")

        with self.assertRaises(UserError) as caught:
            october.action_close()
        self.assertIn("2026-09", str(caught.exception))

        self.make_return(date_from="2026-09-01", date_to="2026-09-30").action_close()
        october.action_close()
        self.assertEqual(october.state, "closed")

    def test_a_first_return_needs_no_predecessor(self):
        # Nothing has ever been closed: the module can start mid-year.
        november = self.make_return(date_from="2026-11-01", date_to="2026-11-30")
        november.action_close()
        self.assertEqual(november.state, "closed")

    def test_the_status_changes_only_through_the_actions(self):
        vat_return = self.make_return()
        with self.assertRaises(UserError):
            vat_return.write({"state": "closed"})
        self.assertEqual(vat_return.state, "draft")

    def test_deleting_a_draft_return_takes_its_manual_entries_with_it(self):
        vat_return = self.make_return()
        vat_return.action_compute()
        typed = vat_return.manual_value_ids.filtered(
            lambda v: v.target_report_line_id.code == "box_23")
        typed.value = 12_345.0

        vat_return.unlink()

        self.assertFalse(typed.exists())
        rendered = self.render.render(self.vat3, self.options())
        self.assertEqual(
            self.box(rendered, "box_23"), 0.0,
            "A deleted return's typed value must not haunt the period.")

    def test_a_closed_return_cannot_be_deleted(self):
        vat_return = self.make_return()
        vat_return.action_close()
        with self.assertRaises(UserError):
            vat_return.unlink()

    def test_reopening_leaves_the_period_locked(self):
        vat_return = self.make_return()
        vat_return.action_close()
        vat_return.action_reopen()

        self.assertEqual(vat_return.state, "draft")
        self.assertEqual(
            self.ke_company.tax_lock_date, self.period_to,
            "Reopening a return must not make a declared period writable "
            "again. Lifting the lock is a separate, deliberate act.")

    def test_a_filed_return_cannot_be_reopened_by_accident(self):
        vat_return = self.make_return()
        vat_return.action_close()
        vat_return.itax_ack_ref = "KRA202608001234"

        with self.assertRaises(UserError) as caught:
            vat_return.action_reopen()
        self.assertIn("KRA202608001234", str(caught.exception))

    def test_two_returns_cannot_cover_the_same_period(self):
        self.make_return()
        # The savepoint keeps the failed INSERT from poisoning the test
        # transaction; without it nothing after this line could touch the
        # database.
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                self.make_return()

    # ------------------------------------------------------ posting the entry

    def test_closing_posts_nothing_unless_the_company_asks_it_to(self):
        self.post_invoice("ST16", 1_000_000.0)
        vat_return = self.make_return()
        vat_return.action_close()

        self.assertFalse(
            vat_return.move_id,
            "Posting is opt-in so a parallel run against an already-filed "
            "period never touches the ledger.")

    def test_posting_on_close_is_refused_while_the_accounts_are_unset(self):
        self.ke_company.ke_vat_post_on_close = True
        self.post_invoice("ST16", 1_000_000.0)

        with self.assertRaises(UserError) as caught:
            self.make_return().action_close()
        self.assertIn("VAT Journal", str(caught.exception))

    def _configure_posting(self):
        company = self.ke_company
        company.ke_vat_post_on_close = True
        company.ke_vat_journal_id = self.company_data["default_journal_misc"]
        company.ke_vat_output_account_id = self.company_data[
            "default_account_tax_sale"]
        company.ke_vat_input_account_id = self.company_data[
            "default_account_tax_purchase"]
        company.ke_vat_payable_account_id = self.company_data[
            "default_account_payable"]
        company.ke_vat_credit_account_id = self.company_data[
            "default_account_receivable"]

    def test_the_posted_entry_clears_output_against_input(self):
        self._configure_posting()
        self.post_invoice("ST16", 1_000_000.0)
        self.post_invoice("PT16", 250_000.0, move_type="in_invoice")

        vat_return = self.make_return()
        vat_return.action_close()

        move = vat_return.move_id
        self.assertTrue(move)
        self.assertEqual(move.state, "posted")
        self.assertAlmostEqual(
            sum(move.line_ids.mapped("debit")),
            sum(move.line_ids.mapped("credit")), places=2,
            msg="Box 18 is box 6 less box 17, so the entry balances by "
                "construction.")
        payable = move.line_ids.filtered(
            lambda l: l.account_id == self.ke_company.ke_vat_payable_account_id)
        self.assertAlmostEqual(payable.credit, 120_000.0, places=2)

    def test_reclosing_reuses_the_vat_entry(self):
        self._configure_posting()
        self.post_invoice("ST16", 1_000_000.0)
        vat_return = self.make_return()
        vat_return.action_close()
        first = vat_return.move_id

        vat_return.action_reopen()
        self.assertEqual(first.state, "draft")
        vat_return.action_close()

        self.assertEqual(
            vat_return.move_id, first,
            "One entry for the life of the return, not a trail of drafts.")
        self.assertEqual(first.state, "posted")
        self.assertEqual(
            self.env["account.move"].search_count([
                ("ref", "=", vat_return.display_name),
                ("company_id", "=", self.ke_company.id)]), 1)

    def _set_manual(self, code, amount):
        expression = (
            self.vat3.line_ids.filtered(lambda l, c=code: l.code == c)
            .expression_ids.filtered(lambda e: e.label == "tax"))
        self.env["account.report.external.value"].create({
            "name": code, "value": amount, "date": self.period_to,
            "target_report_expression_id": expression.id,
            "company_id": self.ke_company.id,
        })

    def test_the_posted_entry_expenses_the_non_deductible_share(self):
        self._configure_posting()
        non_deductible = self.company_data["default_account_expense"]
        self.ke_company.ke_vat_non_deductible_account_id = non_deductible
        # Half the turnover is exempt, so half of the mixed pool is lost.
        self.post_invoice("ST16", 500_000.0)
        self.post_invoice("STEX", 500_000.0)
        self.post_invoice("PT16", 250_000.0, move_type="in_invoice")
        self._set_manual("box_15", 40_000.0)   # mixed-use input VAT -> box 16 = 20,000
        self._set_manual("box_14", 5_000.0)    # exempt-only input VAT

        vat_return = self.make_return()
        vat_return.action_close()
        move = vat_return.move_id

        by_account = {
            line.account_id: line for line in move.line_ids}
        self.assertAlmostEqual(
            by_account[self.ke_company.ke_vat_output_account_id].debit, 80_000.0, places=2)
        self.assertAlmostEqual(
            by_account[self.ke_company.ke_vat_input_account_id].credit, 40_000.0, places=2,
            msg="The input VAT account is cleared by what purchases posted to "
                "it (box 12), not by the deductible figure.")
        self.assertAlmostEqual(
            by_account[non_deductible].debit, 25_000.0, places=2,
            msg="Boxes 14 and 16 are expensed.")
        self.assertAlmostEqual(
            by_account[self.ke_company.ke_vat_payable_account_id].credit, 65_000.0, places=2,
            msg="The balance is box 18: 80,000 - (40,000 - 5,000 - 20,000).")
        self.assertAlmostEqual(
            sum(move.line_ids.mapped("debit")), sum(move.line_ids.mapped("credit")), places=2)

    def test_the_non_deductible_account_is_only_demanded_when_needed(self):
        self._configure_posting()
        self.ke_company.ke_vat_non_deductible_account_id = False
        self.post_invoice("ST16", 1_000_000.0)
        # No box 14 or 16: closes without the account.
        self.make_return().action_close()

        self.post_invoice("ST16", 300_000.0, date="2026-09-05")
        self.post_invoice("STEX", 300_000.0, date="2026-09-05")
        expression = (
            self.vat3.line_ids.filtered(lambda l: l.code == "box_15")
            .expression_ids.filtered(lambda e: e.label == "tax"))
        self.env["account.report.external.value"].create({
            "name": "box_15", "value": 10_000.0, "date": "2026-09-30",
            "target_report_expression_id": expression.id,
            "company_id": self.ke_company.id,
        })
        september = self.make_return(date_from="2026-09-01", date_to="2026-09-30")
        with self.assertRaises(UserError) as caught:
            september.action_close()
        self.assertIn("Non-deductible VAT Account", str(caught.exception))

    def test_the_entry_balances_when_the_apportionment_leaves_fractions(self):
        self._configure_posting()
        self.ke_company.ke_vat_non_deductible_account_id = self.company_data[
            "default_account_expense"]
        # Taxable share 1/3: box 16 = 40,000 * 2/3 = 26,666.666...
        self.post_invoice("ST16", 100_000.0)
        self.post_invoice("STEX", 200_000.0)
        self.post_invoice("PT16", 250_000.0, move_type="in_invoice")
        self._set_manual("box_15", 40_000.0)

        vat_return = self.make_return()
        vat_return.action_close()
        move = vat_return.move_id

        self.assertEqual(move.state, "posted")
        self.assertAlmostEqual(
            sum(move.line_ids.mapped("debit")), sum(move.line_ids.mapped("credit")),
            places=2, msg="Rounding each line on its own can leave the entry a "
                          "cent out; the balancing line is derived from the "
                          "rounded lines instead.")

    def test_a_month_of_net_credit_notes_posts_on_the_right_side(self):
        # Output VAT itself comes out negative here. Every amount has to land
        # on the correct side rather than as a negative debit.
        self._configure_posting()
        self.post_invoice("ST16", 100_000.0)
        self.post_invoice("ST16", 400_000.0, move_type="out_refund")

        vat_return = self.make_return()
        vat_return.action_close()

        move = vat_return.move_id
        self.assertTrue(move)
        self.assertFalse(
            move.line_ids.filtered(lambda l: l.debit < 0 or l.credit < 0),
            "No line may carry a negative debit or credit.")
        self.assertAlmostEqual(
            sum(move.line_ids.mapped("debit")),
            sum(move.line_ids.mapped("credit")), places=2)
