# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Pay now with M-Pesa: the prompt, M-PESA's answer, the payment, the bank."""

from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, JsonRpcException, tagged

from odoo.addons.payment import utils as payment_utils
from odoo.addons.x_ke_mpesa.lib import daraja
from odoo.addons.x_ke_mpesa.tests.common import PHONE, MpesaCommon

STK_PATH = "/mpesa/stkpush/v1/processrequest"


@tagged("post_install", "-at_install")
class TestOnlinePayment(MpesaCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.method = cls.env.ref("payment.payment_method_mpesa")
        # Payment providers are an administrator's business.
        cls.provider = cls.env.ref("x_ke_mpesa_payment.payment_provider_mpesa").sudo().copy({
            "company_id": cls.company.id,
            "mpesa_shortcode_id": cls.paybill.id,
        })
        # Switched on as an administrator does, which activates the M-Pesa method.
        cls.provider.write({"state": "test", "is_published": True})
        cls.company.mpesa_rounding_account_id = cls.company_data["default_account_expense"]

    def online_payment(self, invoice, amount=None):
        # As the payment form's routes create it.
        return self.env["payment.transaction"].sudo().create({
            "provider_id": self.provider.id,
            "payment_method_id": self.method.id,
            "amount": amount or invoice.amount_residual,
            "currency_id": invoice.currency_id.id,
            "partner_id": invoice.partner_id.id,
            "operation": "online_direct",
            "invoice_ids": [(6, 0, invoice.ids)],
        })

    def stk_requests(self):
        return [body for path, body in self.fake.requests if path == STK_PATH]

    def test_provider(self):
        journal = self.paybill.journal_id
        self.assertEqual(self.provider.journal_id, journal)
        line = journal.inbound_payment_method_line_ids.filtered(
            lambda l: l.payment_provider_id == self.provider)
        manual = journal.inbound_payment_method_line_ids.filtered(
            lambda l: not l.payment_provider_id)
        self.assertEqual(line.payment_account_id, manual.payment_account_id[:1],
                         "payments wait on the paybill's outstanding receipts account")
        self.assertEqual(self.provider.available_currency_ids, self.env.ref("base.KES"))
        self.assertIn(self.method, self.provider.payment_method_ids)
        self.assertTrue(self.method.active)

    def test_a_provider_needs_its_shortcode(self):
        with self.assertRaises(ValidationError):
            self.provider.copy({"mpesa_shortcode_id": False, "state": "test"})

    def test_pay_an_invoice(self):
        invoice = self.invoice(1000)
        tx = self.online_payment(invoice)
        self.assertEqual(tx._mpesa_send_stk(PHONE), "pending")
        body, = self.stk_requests()
        self.assertEqual(body["Amount"], 1000)
        self.assertEqual(body["BusinessShortCode"], "600200")
        self.assertEqual(body["PartyB"], "600200")
        self.assertEqual(body["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(body["AccountReference"], daraja.account_reference(invoice.name))
        self.assertIn("2547*****149", tx.state_message)
        request = tx.mpesa_transaction_id
        self.assertEqual(request.payment_transaction_id, tx)
        self.assertEqual(tx.provider_reference, request.checkout_request_id)
        self.assertFalse(request.move_id, "the payment pays the invoice, not the receipt")

        self.deliver()
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.provider_reference, request.receipt)
        self.assertEqual(request.allocation_state, "unallocated",
                         "the receipt waits for the payment Odoo is about to create")
        self.assertEqual(invoice.payment_state, "not_paid")

        tx._post_process()
        payment = tx.payment_id
        self.assertEqual(payment.journal_id, self.paybill.journal_id)
        self.assertEqual(payment.amount, 1000)
        self.assertEqual(request.allocation_state, "allocated")
        self.assertTrue(request.statement_line_id.is_reconciled)
        outstanding = payment.move_id.line_ids.filtered(
            lambda l: l.account_id == payment.outstanding_account_id)
        self.assertTrue(outstanding.reconciled, "the receipt closed the payment's outstanding line")
        self.assertEqual(invoice.payment_state, "paid")

    def test_cents(self):
        invoice = self.invoice(1000.40)
        tx = self.online_payment(invoice)
        tx._mpesa_send_stk(PHONE)
        self.assertEqual(self.stk_requests()[0]["Amount"], 1000, "M-PESA takes whole shillings")
        self.deliver()
        self.assertEqual(tx.state, "done", "whole shillings of the amount are the amount")
        tx._post_process()
        self.assertEqual(tx.payment_id.amount, 1000.40)
        self.assertEqual(invoice.payment_state, "paid")
        line = tx.mpesa_transaction_id.statement_line_id
        self.assertTrue(line.is_reconciled)
        rounding = line.move_id.line_ids.filtered(
            lambda l: l.account_id == self.company.mpesa_rounding_account_id)
        self.assertAlmostEqual(rounding.balance, 0.40)

    def test_customer_cancels(self):
        invoice = self.invoice(500)
        tx = self.online_payment(invoice)
        self.fake.script_stk(result_code=1032)
        tx._mpesa_send_stk(PHONE)
        self.deliver()
        self.assertEqual(tx.state, "cancel")
        self.assertTrue(tx.state_message)
        tx._post_process()
        self.assertFalse(tx.payment_id)
        self.assertEqual(invoice.payment_state, "not_paid")

    def test_wrong_pin(self):
        tx = self.online_payment(self.invoice(500))
        self.fake.script_stk(result_code=2001)
        tx._mpesa_send_stk(PHONE)
        self.deliver()
        self.assertEqual(tx.state, "error")
        self.assertIn("PIN", tx.state_message)

    def test_paid_a_different_amount(self):
        invoice = self.invoice(1000)
        tx = self.online_payment(invoice)
        self.fake.script_stk(amount=900)
        tx._mpesa_send_stk(PHONE)
        self.deliver()
        self.assertEqual(tx.state, "error")
        request = tx.mpesa_transaction_id
        self.assertEqual(request.state, "confirmed", "the money is in")
        self.assertEqual(request.exception, "amount_mismatch")
        self.assertEqual(request.allocation_state, "unallocated", "the queue decides")
        self.assertEqual(invoice.payment_state, "not_paid")

    def test_double_click_prompts_once(self):
        tx = self.online_payment(self.invoice(300))
        tx._mpesa_send_stk(PHONE)
        tx._mpesa_send_stk(PHONE)
        self.assertEqual(len(self.stk_requests()), 1)

    def test_daraja_refuses(self):
        tx = self.online_payment(self.invoice(300))
        self.fake.fail_next[STK_PATH] = (400, {"requestId": "x", "errorCode": "400.002.02",
                                               "errorMessage": "Bad Request - Invalid PhoneNumber"})
        self.assertEqual(tx._mpesa_send_stk(PHONE), "error")
        self.assertTrue(tx.state_message)

    def test_a_bad_number(self):
        tx = self.online_payment(self.invoice(300))
        with self.assertRaises(Exception):
            tx._mpesa_send_stk("12")
        self.assertEqual(tx.state, "draft")
        self.assertFalse(self.stk_requests())

    def test_processed_payments_are_not_prompted_again(self):
        tx = self.online_payment(self.invoice(300))
        tx._mpesa_send_stk(PHONE)
        self.deliver()
        with self.assertRaisesRegex(ValidationError, "already processed"):
            tx._mpesa_send_stk(PHONE)


@tagged("post_install", "-at_install")
class TestPromptRoute(HttpCase):
    """/payment/mpesa/stk only acts for the browser that started the payment."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.company
        kes = cls.env.ref("base.KES")
        kes.active = True
        journal = cls.env["account.journal"].create({
            "name": "M-Pesa route test", "type": "bank", "code": "MPRT"})
        paybill = cls.env["mpesa.shortcode"].create({
            "kind": "paybill", "number": "600990", "company_id": company.id,
            "journal_id": journal.id,
        })
        provider = cls.env.ref("x_ke_mpesa_payment.payment_provider_mpesa").copy({
            "company_id": company.id, "mpesa_shortcode_id": paybill.id, "state": "test"})
        cls.tx = cls.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": cls.env.ref("payment.payment_method_mpesa").id,
            "amount": 100,
            "currency_id": kes.id,
            "partner_id": cls.env.ref("base.partner_admin").id,
            "operation": "online_direct",
        })

    def test_route(self):
        Tx = type(self.env["payment.transaction"])
        calls = []

        def fake_send(tx, phone):
            calls.append((tx.reference, phone))
            return "pending"

        with patch.object(Tx, "_mpesa_send_stk", fake_send):
            with self.assertRaises(JsonRpcException):
                self.make_jsonrpc_request("/payment/mpesa/stk", {
                    "reference": self.tx.reference, "access_token": "forged",
                    "phone": PHONE})
            self.assertFalse(calls)
            result = self.make_jsonrpc_request("/payment/mpesa/stk", {
                "reference": self.tx.reference,
                "access_token": payment_utils.generate_access_token(self.tx.reference,
                                                                    env=self.env),
                "phone": PHONE})
        self.assertEqual(result, {"state": "pending"})
        self.assertEqual(calls, [(self.tx.reference, PHONE)])

    def test_processing_values_carry_the_token(self):
        values = self.tx._get_processing_values()
        self.assertEqual(values["mpesa_access_token"],
                         payment_utils.generate_access_token(self.tx.reference, env=self.env))
