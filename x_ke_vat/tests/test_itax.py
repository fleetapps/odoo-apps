# -*- coding: utf-8 -*-
"""Reconciling a KRA CSV against the ledger.

The five match states are the deliverable. "Our output VAT is 4,312 off" is not
actionable; "these three invoices are in KRA and not here, and this one has the
wrong PIN against it" is.

Re-importing has to converge rather than accumulate, because KRA loads these
files in batches through the month and the same section gets downloaded several
times before anyone files.
"""

import base64

from odoo.tests import tagged

from .common import KeVatCase

HEADER = "Invoice No,Invoice Date,PIN of Purchaser,Taxable Value,Amount of VAT"


def csv_bytes(*rows):
    return base64.b64encode(
        "\n".join([HEADER] + list(rows)).encode("utf-8"))


@tagged("post_install_l10n", "post_install", "-at_install")
class TestItaxReconciliation(KeVatCase):

    def setUp(self):
        super().setUp()
        self.vat_return = self.make_return()
        # A walk-in customer with no PIN and an overseas supplier: the two
        # discriminators that decide which section a document belongs to.
        self.walk_in = self.env["res.partner"].create({
            "name": "Walk-in Customer",
            "country_id": self.env.ref("base.ke").id,
        })
        self.overseas = self.env["res.partner"].create({
            "name": "Shenzhen Components Co",
            "country_id": self.env.ref("base.cn").id,
        })

    def _batch(self, *rows, section="sales_pin"):
        batch = self.env["ke.vat.itax.batch"].create({
            "return_id": self.vat_return.id,
            "section": section,
            "csv_file": csv_bytes(*rows),
            "csv_filename": "sec_b_with_vat_pin1.csv",
        })
        batch.action_import()
        return batch

    def _row(self, move, taxable=None, tax=None, pin=None):
        return "%s,15/08/2026,%s,%s,%s" % (
            move.name,
            pin or move.partner_id.vat,
            taxable if taxable is not None else move.amount_untaxed,
            tax if tax is not None else move.amount_tax,
        )

    # -------------------------------------------------------- the five states

    def test_an_invoice_kra_agrees_with_is_matched(self):
        move = self.post_invoice("ST16", 100_000.0, date="2026-08-15")
        batch = self._batch(self._row(move))

        self.assertEqual(len(batch.line_ids), 1)
        self.assertEqual(batch.line_ids.match_state, "matched")
        self.assertEqual(batch.line_ids.move_id, move)
        self.assertEqual(batch.matched_count, 1)
        self.assertEqual(batch.exception_count, 0)

    def test_an_invoice_only_kra_has_is_flagged(self):
        batch = self._batch(
            "INV/2026/09999,15/08/2026,P051234567X,100000,16000")

        self.assertEqual(batch.line_ids.match_state, "kra_only")
        self.assertFalse(batch.line_ids.move_id)

    def test_an_invoice_only_we_have_is_flagged_and_totalled(self):
        move = self.post_invoice("ST16", 100_000.0, date="2026-08-15")
        batch = self._batch()  # KRA has nothing yet

        self.assertEqual(batch.line_ids.match_state, "odoo_only")
        self.assertEqual(batch.line_ids.move_id, move)
        self.assertEqual(
            batch.lumpsum_amount, 100_000.0,
            "Untransmitted sales still have to be declared, so the figure for "
            "the lumpsum field falls out of the reconciliation.")

    def test_a_different_amount_is_flagged_rather_than_matched(self):
        move = self.post_invoice("ST16", 100_000.0, date="2026-08-15")
        batch = self._batch(self._row(move, taxable=95_000.0, tax=15_200.0))

        self.assertEqual(batch.line_ids.match_state, "amount_variance")
        self.assertEqual(batch.line_ids.kra_taxable_amount, 95_000.0)
        self.assertEqual(batch.line_ids.odoo_taxable_amount, 100_000.0)

    def test_a_different_pin_is_flagged_rather_than_matched(self):
        move = self.post_invoice("ST16", 100_000.0, date="2026-08-15")
        batch = self._batch(self._row(move, pin="P050000000Z"))

        self.assertEqual(
            batch.line_ids.match_state, "pin_variance",
            "A supplier capturing the wrong PIN is a named reason to leave "
            "input tax unclaimed, so it cannot be silently treated as matched.")

    # ------------------------------------------------------------ importing

    def test_reimporting_a_fuller_file_converges_rather_than_duplicates(self):
        first = self.post_invoice("ST16", 100_000.0, date="2026-08-10")
        second = self.post_invoice("ST16", 250_000.0, date="2026-08-20")

        batch = self._batch(self._row(first))
        self.assertEqual(len(batch.line_ids), 2)  # one matched, one odoo_only

        # KRA's next batch load includes the second invoice.
        batch.csv_file = csv_bytes(self._row(first), self._row(second))
        batch.action_import()

        self.assertEqual(len(batch.line_ids), 2)
        self.assertEqual(batch.matched_count, 2)
        self.assertEqual(batch.exception_count, 0)
        self.assertEqual(batch.lumpsum_amount, 0.0)

    def test_a_file_without_an_invoice_number_column_says_so(self):
        from odoo.exceptions import UserError
        batch = self.env["ke.vat.itax.batch"].create({
            "return_id": self.vat_return.id,
            "section": "sales_pin",
            "csv_file": base64.b64encode(b"Something,Else\n1,2"),
            "csv_filename": "wrong.csv",
        })
        with self.assertRaises(UserError) as caught:
            batch.action_import()
        self.assertIn("Something", str(caught.exception))

    def test_reimporting_keeps_a_deferral_and_a_note(self):
        bill = self.post_invoice(
            "PT16", 100_000.0, move_type="in_invoice", date="2026-08-15")
        batch = self._batch(
            "%s,15/08/2026,%s,100000,16000" % (bill.name, self.supplier.vat),
            section="purchases_local")
        line = batch.line_ids
        self.assertEqual(line.match_state, "matched")
        line.write({"claim_state": "defer", "note": "await certificate"})

        # KRA's fuller file lands; the row is still there, unchanged.
        batch.csv_file = csv_bytes(
            "%s,15/08/2026,%s,100000,16000" % (bill.name, self.supplier.vat))
        batch.action_import()

        self.assertEqual(len(batch.line_ids), 1)
        self.assertTrue(line.exists(), "The row was updated in place, not replaced.")
        self.assertEqual(line.claim_state, "defer")
        self.assertEqual(line.note, "await certificate")

    def test_a_decision_on_an_odoo_only_row_moves_to_the_kra_row_that_replaces_it(self):
        bill = self.post_invoice(
            "PT16", 100_000.0, move_type="in_invoice", date="2026-08-15")
        batch = self._batch(section="purchases_local")   # KRA has nothing yet
        odoo_only = batch.line_ids
        self.assertEqual(odoo_only.match_state, "odoo_only")
        odoo_only.write({"claim_state": "defer", "note": "await certificate"})

        batch.csv_file = csv_bytes(
            "%s,15/08/2026,%s,100000,16000" % (bill.name, self.supplier.vat))
        batch.action_import()

        matched = batch.line_ids
        self.assertEqual(matched.match_state, "matched")
        self.assertEqual(
            matched.claim_state, "defer",
            "The deferral is about the bill, not about which side first "
            "reported it.")
        self.assertEqual(matched.note, "await certificate")

    def test_a_row_kra_dropped_is_removed_on_reimport(self):
        first = self.post_invoice("ST16", 100_000.0, date="2026-08-10")
        second = self.post_invoice("ST16", 250_000.0, date="2026-08-20")
        batch = self._batch(self._row(first), self._row(second))
        self.assertEqual(batch.matched_count, 2)

        batch.csv_file = csv_bytes(self._row(first))
        batch.action_import()

        self.assertEqual(batch.matched_count, 1)
        self.assertEqual(
            batch.line_ids.filtered(lambda l: l.move_id == second).match_state,
            "odoo_only")

    def test_two_suppliers_sharing_a_bill_number_are_told_apart(self):
        other = self.env["res.partner"].create({
            "name": "Kisumu Hardware Ltd", "vat": "P051111111A",
            "country_id": self.env.ref("base.ke").id,
        })
        bill_a = self.post_invoice(
            "PT16", 10_000.0, move_type="in_invoice", date="2026-08-10",
            ref="INV-001")
        bill_b = self.post_invoice(
            "PT16", 20_000.0, move_type="in_invoice", date="2026-08-11",
            partner=other, ref="INV-001")

        batch = self._batch(
            "INV-001,10/08/2026,%s,10000,1600" % self.supplier.vat,
            "INV-001,11/08/2026,%s,20000,3200" % other.vat,
            section="purchases_local")

        by_pin = {line.kra_pin: line for line in batch.line_ids}
        self.assertEqual(by_pin[self.supplier.vat].move_id, bill_a)
        self.assertEqual(by_pin[other.vat].move_id, bill_b)
        self.assertEqual(set(batch.line_ids.mapped("match_state")), {"matched"})

    def test_a_foreign_currency_invoice_is_compared_in_shillings(self):
        usd = self.setup_other_currency("USD", rates=[("2026-01-01", 0.01)])
        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.customer.id,
            "invoice_date": "2026-08-15",
            "date": "2026-08-15",
            "currency_id": usd.id,
            "company_id": self.ke_company.id,
            "invoice_line_ids": [(0, 0, {
                "name": "Export", "quantity": 1, "price_unit": 1_000.0,
                "tax_ids": [(6, 0, self.ke_tax("ST16").ids)],
            })],
        })
        move.action_post()
        self.assertEqual(move.amount_untaxed, 1_000.0, "USD on the document")
        self.assertAlmostEqual(move.amount_untaxed_signed, 100_000.0, places=2)

        # KRA reports it in shillings.
        batch = self._batch("%s,15/08/2026,%s,100000,16000" % (move.name, self.customer.vat))

        self.assertEqual(batch.line_ids.match_state, "matched")
        self.assertAlmostEqual(
            batch.line_ids.odoo_taxable_amount, 100_000.0, places=2,
            msg="Our figure is shown in the company currency, like KRA's.")

    def test_a_listed_supplier_addressed_to_a_contact_is_still_excluded(self):
        self.supplier.ke_vat_special_table = True
        contact = self.env["res.partner"].create({
            "name": "Accounts Clerk", "parent_id": self.supplier.id,
            "type": "contact",
        })
        self.post_invoice(
            "PT16", 100_000.0, move_type="in_invoice", date="2026-08-15",
            partner=contact)
        batch = self._batch(section="purchases_local")

        self.assertEqual(batch.line_ids.claim_state, "exclude_special_table")

    def test_a_kra_only_row_from_a_listed_supplier_is_excluded_by_pin(self):
        self.supplier.ke_vat_special_table = True
        batch = self._batch(
            "INV-777,15/08/2026,%s,50000,8000" % self.supplier.vat,
            section="purchases_local")

        self.assertEqual(batch.line_ids.match_state, "kra_only")
        self.assertEqual(
            batch.line_ids.claim_state, "exclude_special_table",
            "A listed supplier's bill that never reached us must not slip "
            "into the claim through the pre-populated return.")

    # ------------------------------------------------ sections stay apart

    def test_a_section_never_reports_a_sibling_sections_documents_as_missing(self):
        # One sale to a PIN customer, one to a walk-in. KRA has the first.
        pin_sale = self.post_invoice("ST16", 100_000.0, date="2026-08-15")
        cash_sale = self.post_invoice(
            "ST16", 5_000.0, date="2026-08-16", partner=self.walk_in)

        registered = self._batch(self._row(pin_sale), section="sales_pin")
        unregistered = self._batch(section="sales_no_pin")

        self.assertEqual(registered.line_ids.mapped("match_state"), ["matched"])
        self.assertEqual(
            registered.lumpsum_amount, 0.0,
            "The walk-in sale belongs to the other section; it is not an "
            "untransmitted registered sale.")
        self.assertEqual(unregistered.line_ids.mapped("match_state"), ["odoo_only"])
        self.assertEqual(unregistered.line_ids.move_id, cash_sale)
        self.assertEqual(
            unregistered.lumpsum_amount, 5_000.0,
            "Only the sale KRA does not have, in the section it belongs to.")

    def test_a_document_matched_by_one_section_is_not_missing_in_another(self):
        pin_sale = self.post_invoice("ST16", 100_000.0, date="2026-08-15")
        # Imported in the wrong order on purpose: the empty no-PIN file first.
        unregistered = self._batch(section="sales_no_pin")
        registered = self._batch(self._row(pin_sale), section="sales_pin")

        self.assertEqual(registered.matched_count, 1)
        self.assertFalse(
            unregistered.line_ids,
            "A PIN customer's invoice is not a no-PIN sale, matched or not.")
        self.assertEqual(unregistered.lumpsum_amount, 0.0)

    def test_a_foreign_supplier_is_an_import_not_a_local_purchase(self):
        bill = self.post_invoice(
            "PT16", 80_000.0, move_type="in_invoice", date="2026-08-12",
            partner=self.overseas)
        local = self._batch(section="purchases_local")
        imports = self._batch(section="purchases_imports")

        self.assertFalse(
            local.line_ids,
            "KRA's local-purchases feed never carries a foreign supplier, so "
            "the bill must not be reported missing from it.")
        self.assertEqual(imports.line_ids.mapped("match_state"), ["odoo_only"])
        self.assertEqual(imports.line_ids.move_id, bill)

    def test_purchases_and_credit_notes_never_feed_the_lumpsum(self):
        self.post_invoice(
            "PT16", 100_000.0, move_type="in_invoice", date="2026-08-15")
        self.post_invoice("ST16", 30_000.0, move_type="out_refund", date="2026-08-15")
        purchases = self._batch(section="purchases_local")
        credit_notes = self._batch(section="sales_credit_notes")

        self.assertEqual(purchases.line_ids.mapped("match_state"), ["odoo_only"])
        self.assertEqual(credit_notes.line_ids.mapped("match_state"), ["odoo_only"])
        self.assertEqual(
            purchases.lumpsum_amount + credit_notes.lumpsum_amount, 0.0,
            "The lumpsum is untransmitted sales. A bill KRA lacks is a claim "
            "we cannot make; a credit note is not a sale.")

    # ------------------------------------------------------- the input rules

    def test_input_tax_is_claimable_for_six_months(self):
        move = self.post_invoice(
            "PT16", 100_000.0, move_type="in_invoice", date="2026-08-15")
        batch = self._batch(section="purchases_local")

        line = batch.line_ids
        self.assertEqual(line.move_id, move)
        self.assertEqual(
            str(line.claim_deadline), "2027-02-28",
            "Six months after the end of the period the supply fell in.")
        self.assertEqual(line.claim_state, "claim")

    def test_a_supplier_on_the_special_table_is_excluded_from_the_claim(self):
        self.supplier.ke_vat_special_table = True
        self.post_invoice(
            "PT16", 100_000.0, move_type="in_invoice", date="2026-08-15")
        batch = self._batch(section="purchases_local")

        self.assertEqual(
            batch.line_ids.claim_state, "exclude_special_table",
            "A return carrying a special-table supplier is rejected at filing.")

    def test_the_special_table_does_not_block_a_credit_note(self):
        self.supplier.ke_vat_special_table = True
        self.post_invoice(
            "PT16", 40_000.0, move_type="in_refund", date="2026-08-15")
        batch = self._batch(section="purchases_credit_notes")

        self.assertNotEqual(
            batch.line_ids.claim_state, "exclude_special_table",
            "KRA relaxes the special-table check for credit notes.")

    def test_sales_lines_carry_no_claim_state(self):
        self.post_invoice("ST16", 100_000.0, date="2026-08-15")
        batch = self._batch()
        self.assertFalse(batch.line_ids.claim_state)
