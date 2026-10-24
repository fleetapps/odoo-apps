# -*- coding: utf-8 -*-
"""The queue: posting queues, the cron signs, failures retry and numbers hold."""

from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from ..backends import EtimsError
from ..backends.demo import DemoAdapter
from ..models.ke_etims_document import MAX_ATTEMPTS
from .common import KeEtimsCase


@tagged("post_install", "-at_install")
class TestQueue(KeEtimsCase):

    def test_posting_queues_and_never_signs(self):
        with patch.object(DemoAdapter, "_sign", side_effect=AssertionError("signed while posting")):
            invoice = self.ke_invoice()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "to_send")
        self.assertEqual(document.kind, "sale")
        self.assertFalse(document.kra_invoice_no, "A number is drawn when sending, not posting.")
        self.assertEqual(invoice.ke_etims_state, "to_send")

    def test_the_cron_signs_and_the_invoice_shows_the_receipt(self):
        invoice = self.ke_invoice()
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "sent")
        self.assertEqual(invoice.ke_etims_state, "sent")
        self.assertEqual(document.kra_invoice_no, 1)
        self.assertTrue(invoice.ke_etims_signature)
        self.assertTrue(invoice.ke_etims_internal_data)
        self.assertEqual(invoice.ke_etims_scu_id, self.device.scu_id)
        self.assertTrue(invoice.ke_etims_qr_url.startswith("https://etims-sbx.kra.go.ke/"))
        self.assertTrue(invoice.ke_etims_qr_image)
        self.assertIn("Signed by eTIMS", invoice.message_ids[0].body)
        self.assertFalse(invoice.show_reset_to_draft_button)

    def test_numbers_are_gapless_per_branch(self):
        first, second = self.ke_invoice(), self.ke_invoice()
        branch = self.make_branch("Nairobi Traders - Kisumu", "01")
        self.make_device(branch, bhf_id="01").action_initialize()
        branch_invoice = self.ke_invoice(company=branch)
        self.run_queue()
        self.assertEqual(first.ke_etims_kra_invoice_no, 1)
        self.assertEqual(second.ke_etims_kra_invoice_no, 2)
        self.assertEqual(branch_invoice.ke_etims_document_id.device_id.bhf_id, "01")
        self.assertEqual(branch_invoice.ke_etims_kra_invoice_no, 1,
                         "Every KRA branch numbers its own invoices from 1.")

    def test_a_branch_trading_under_head_office_uses_its_device(self):
        branch = self.make_branch("Nairobi Traders - Shop", "00")
        invoice = self.ke_invoice(company=branch)
        self.assertEqual(invoice.ke_etims_document_id.device_id, self.device)

    def test_a_transient_failure_backs_off_and_keeps_the_number(self):
        invoice = self.ke_invoice()
        timeout = EtimsError("KRA did not answer in time.", retryable=True,
                             may_have_been_processed=True)
        with self.demo_fails(timeout):
            self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "to_send")
        self.assertEqual(document.attempts, 1)
        self.assertEqual(document.kra_invoice_no, 1)
        self.assertGreater(document.next_try, fields.Datetime.now())
        self.assertTrue(document.may_have_been_processed)
        # The retry sends the same number and signs.
        self.run_queue()
        self.assertEqual(document.state, "sent")
        self.assertEqual(document.kra_invoice_no, 1)
        self.assertEqual(self.device.next_invoice_no, 2)

    def test_eight_failures_end_in_failed_and_an_activity(self):
        invoice = self.ke_invoice()
        down = EtimsError("Could not connect.", retryable=True)
        with self.demo_fails(down):
            for _attempt in range(MAX_ATTEMPTS):
                self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "failed")
        self.assertEqual(document.attempts, MAX_ATTEMPTS)
        activity = invoice.activity_ids.filtered(
            lambda a: a.summary == invoice._ke_etims_activity_summary())
        self.assertEqual(activity.user_id, self.salesperson)
        # Nothing reached KRA, so the number went back: the next invoice is 1.
        self.assertFalse(document.kra_invoice_no)
        self.assertEqual(self.device.next_invoice_no, 1)
        # Sending again later works and closes the activity.
        document.action_send_now()
        self.assertEqual(document.state, "sent")
        self.assertFalse(invoice.activity_ids.filtered(
            lambda a: a.summary == invoice._ke_etims_activity_summary()))

    def test_a_rejection_fails_at_once_and_releases_the_number(self):
        invoice = self.ke_invoice()
        with self.demo_fails(EtimsError("Request parameter error", code="910")):
            self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "failed")
        self.assertEqual(document.attempts, 1)
        self.assertFalse(document.kra_invoice_no)
        self.assertEqual(self.device.next_invoice_no, 1)
        self.assertIn("910", document.last_error_code)

    def test_a_duplicate_keeps_the_number_for_a_receipt_recorded_by_hand(self):
        invoice = self.ke_invoice()
        with self.demo_fails(EtimsError("Overlapped data", code="994", duplicate=True)):
            self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "failed")
        self.assertEqual(document.kra_invoice_no, 1)
        with self.assertRaises(UserError):
            document.action_mark_signed()
        document.write({"receipt_no": 1, "total_receipt_no": 1, "signature": "ABCDEFGHIJKLMNOP",
                        "internal_data": "QRSTUVWXYZ234567QRSTUVWXYZ"})
        document.action_mark_signed()
        self.assertEqual(invoice.ke_etims_state, "sent")
        self.assertIn("ABCDEFGHIJKLMNOP", invoice.ke_etims_qr_url)

    def test_validation_fails_before_a_number_is_drawn(self):
        unclassified = self.make_product("Mystery goods", 500.0, self.st16, item_class=False)
        invoice = self.ke_invoice(products=unclassified)
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "failed")
        self.assertFalse(document.kra_invoice_no)
        self.assertIn("KRA item class", document.last_error)
        self.assertIn("ke_etims_failed", invoice._get_alerts())

    def test_a_bad_customer_pin_is_refused(self):
        self.customer.vat = "NOT-A-PIN"
        invoice = self.ke_invoice()
        self.run_queue()
        self.assertIn("not a KRA PIN", invoice.ke_etims_document_id.last_error)

    def test_products_register_themselves_on_first_sale(self):
        template = self.product.product_tmpl_id
        self.assertFalse(template.ke_etims_registration_ids)
        self.ke_invoice()
        self.run_queue()
        registration = template.ke_etims_registration_ids
        self.assertEqual(registration.device_id, self.device)
        self.assertTrue(template.ke_etims_item_code.startswith("KE2NTU"))
        self.assertTrue(template.with_company(self.ke_company).ke_etims_registered)

    def test_sale_amounts_are_the_invoice_vat_inclusive_in_shillings(self):
        cheap = self.make_product("Exempt maize", 200.0, self.ke_tax("STEX"))
        invoice = self.ke_invoice(products=self.product + cheap)
        document = invoice.ke_etims_document_id
        document.kra_invoice_no = 1
        document.confirmed_at = fields.Datetime.now()
        for template in (self.product | cheap).product_tmpl_id:
            template._ke_etims_register(self.device)
        sale = invoice._ke_etims_prepare(document)
        totals = sale.totals()
        self.assertEqual(float(totals.total_amount), invoice.amount_total)
        self.assertEqual(float(totals.total_tax), invoice.amount_tax)
        by_type = {line.tax_type: line for line in sale.lines}
        self.assertEqual(set(by_type), {"B", "A"})
        b_line = invoice.invoice_line_ids.filtered(lambda l: l.product_id == self.product)
        self.assertEqual(float(by_type["B"].taxable_amount), b_line.price_total)
        self.assertEqual(float(by_type["B"].tax_amount),
                         round(b_line.price_total - b_line.price_subtotal, 2))

    def test_signed_invoices_cannot_go_back_to_draft(self):
        invoice = self.ke_invoice()
        self.run_queue()
        with self.assertRaisesRegex(UserError, "credit note"):
            invoice.button_draft()

    def test_an_unsent_invoice_reset_to_draft_cancels_its_document(self):
        invoice = self.ke_invoice()
        document = invoice.ke_etims_document_id
        invoice.button_draft()
        self.assertEqual(document.state, "cancelled")
        invoice.action_post()
        self.assertNotEqual(invoice.ke_etims_document_id, document)
        self.assertEqual(invoice.ke_etims_document_id.state, "to_send")

    def test_a_credit_note_reverses_a_signed_invoice_with_a_reason(self):
        invoice = self.ke_invoice()
        self.run_queue()
        refund = self.credit(invoice)
        document = refund.ke_etims_document_id
        self.assertEqual(document.kind, "credit_note")
        self.assertEqual(document.reason_code, "06")
        self.run_queue()
        self.assertEqual(document.state, "sent")
        self.assertEqual(document.original_document_id, invoice.ke_etims_document_id)
        self.assertEqual(document.kra_invoice_no, 2)
        self.assertEqual(document.receipt_label, "NC")

    def test_a_credit_note_of_an_unsigned_invoice_waits_for_it(self):
        self.env.company.ke_etims_auto_send = False
        invoice = self.ke_invoice()
        self.env.company.ke_etims_auto_send = True
        refund = self.credit(invoice)
        self.run_queue()
        self.assertEqual(refund.ke_etims_document_id.state, "failed")
        self.assertIn("has not been signed", refund.ke_etims_document_id.last_error)

    def test_send_now_from_the_invoice(self):
        self.env.company.ke_etims_auto_send = False
        invoice = self.ke_invoice()
        self.assertEqual(invoice.ke_etims_state, "not_sent")
        invoice.action_ke_etims_send()
        self.assertEqual(invoice.ke_etims_state, "sent")

    def test_the_send_dialog_tries_once_and_warns_if_still_unsigned(self):
        invoice = self.ke_invoice()
        with self.demo_fails(EtimsError("down", retryable=True)):
            invoice.action_invoice_sent()
        self.assertEqual(invoice.ke_etims_state, "to_send")
        wizard = self.env["account.move.send.wizard"].with_context(
            active_model="account.move", active_ids=invoice.ids).create({})
        self.assertIn("ke_etims_unsigned", wizard.alerts)
        invoice.action_invoice_sent()
        self.assertEqual(invoice.ke_etims_state, "sent")

    def test_a_strict_device_sends_one_number_at_a_time(self):
        with patch.object(DemoAdapter, "strict_sequence", True):
            first, second = self.ke_invoice(), self.ke_invoice()
            with self.demo_fails(EtimsError("timeout", retryable=True,
                                            may_have_been_processed=True)):
                self.run_queue()
            self.assertEqual(first.ke_etims_kra_invoice_no, 1)
            self.assertFalse(second.ke_etims_kra_invoice_no,
                             "Nothing after an unsigned number may be numbered.")
            self.assertEqual(self.device.blocking_document_id, first.ke_etims_document_id)
            self.run_queue()
        self.assertEqual(first.ke_etims_state, "sent")
        self.assertEqual(second.ke_etims_state, "sent")
        self.assertEqual(second.ke_etims_kra_invoice_no, 2)

    def test_a_stale_sending_document_is_picked_up_again(self):
        invoice = self.ke_invoice()
        document = invoice.ke_etims_document_id
        document.write({"state": "sending", "kra_invoice_no": self.device._ke_etims_next_invoice_no()})
        document.flush_recordset()
        self.env.cr.execute(
            "UPDATE ke_etims_document SET write_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(hours=1), document.id])
        document.invalidate_recordset()
        self.run_queue()
        self.assertEqual(document.state, "sent")
        self.assertEqual(document.kra_invoice_no, 1)

    def test_tax_types_come_from_the_supply_classification(self):
        expected = {"ST16": "B", "ST8": "E", "ST0": "C", "STEX": "A"}
        for template_id, band in expected.items():
            self.assertEqual(self.ke_tax(template_id).ke_etims_tax_type_id.code, band, template_id)
        withholding = self.env["account.tax"].search([
            ("company_id", "=", self.ke_company.id), ("name", "ilike", "withh")], limit=1)
        if withholding:
            self.assertFalse(withholding.ke_etims_tax_type_id)

    def test_production_needs_a_certification(self):
        with self.assertRaises(ValidationError) as caught:
            self.make_device(self.ke_company, backend="digitax", environment="production",
                             bhf_id="02", api_key="live")
        self.assertIn("certification", str(caught.exception))
        device = self.make_device(self.ke_company, backend="digitax", environment="production",
                                  bhf_id="02", api_key="live", certification_ref="DT-KRA-2026-01")
        self.assertEqual(device.environment, "production")
        with self.assertRaises(ValidationError) as caught:
            self.make_device(self.ke_company, backend="demo", environment="test", bhf_id="03")
        self.assertIn("Demo environment", str(caught.exception))

    def test_reinitialising_is_needed_after_pointing_elsewhere(self):
        self.assertEqual(self.device.state, "initialised")
        self.device.serial = "NEW-SERIAL"
        self.assertEqual(self.device.state, "draft")

    def test_a_device_not_initialised_yet_holds_its_queue(self):
        self.device.active = False
        device = self.make_device(self.ke_company)
        invoice = self.ke_invoice()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.device_id, device)
        self.run_queue()
        self.assertEqual(document.state, "to_send", "Crons only touch initialised devices.")
        self.assertFalse(document.kra_invoice_no)
        device.action_initialize()
        self.run_queue()
        self.assertEqual(invoice.ke_etims_state, "sent")

    def test_the_reversal_wizard_asks_for_the_reason(self):
        invoice = self.ke_invoice()
        wizard = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=invoice.ids).create(
            {"journal_id": invoice.journal_id.id})
        self.assertTrue(wizard.ke_etims_required)
