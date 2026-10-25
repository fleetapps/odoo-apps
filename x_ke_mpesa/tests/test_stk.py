# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""STK Push: what is sent, and every way the answer comes back."""

from datetime import timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..lib import daraja
from .common import PHONE, MpesaCommon


@tagged("post_install", "-at_install")
class TestStkPush(MpesaCommon):

    def sent(self, path="/mpesa/stkpush/v1/processrequest"):
        return [body for p, body in self.fake.requests if p == path]

    def test_till_request(self):
        tx = self.stk(150)
        body = self.sent()[-1]
        self.assertEqual(body["BusinessShortCode"], "174379", "a till signs with its store")
        self.assertEqual(body["PartyB"], "600100", "and credits itself")
        self.assertEqual(body["TransactionType"], "CustomerBuyGoodsOnline")
        self.assertEqual(body["PartyA"], "254708374149")
        self.assertEqual(body["PhoneNumber"], "254708374149")
        self.assertEqual(body["Amount"], 150)
        self.assertIsInstance(body["Amount"], int)
        self.assertEqual(body["Password"], daraja.stk_password(
            "174379", self.store.sudo().passkey, body["Timestamp"]))
        self.assertIn("/pay/ke/", body["CallBackURL"])
        self.assertTrue(body["CallBackURL"].endswith("/stk"))
        self.assertEqual(tx.state, "requested")
        self.assertTrue(tx.checkout_request_id.startswith("ws_CO_"))
        self.assertEqual(tx.channel, "stk")

    def test_paybill_request(self):
        self.stk(200, shortcode=self.paybill, reference="INV/2026/0001")
        body = self.sent()[-1]
        self.assertEqual(body["BusinessShortCode"], "600200")
        self.assertEqual(body["PartyB"], "600200")
        self.assertEqual(body["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(body["AccountReference"], "INV20260001")

    def test_decimals_are_never_sent(self):
        tx = self.stk(150.4)
        self.assertEqual(self.sent()[-1]["Amount"], 150)
        self.assertEqual(tx.amount, 150)

    def test_less_than_a_shilling_is_refused(self):
        with self.assertRaises(UserError):
            self.stk(0.4)

    def test_bad_phone_is_refused(self):
        with self.assertRaises(UserError):
            self.stk(10, phone="0208374149")

    def test_success_callback(self):
        self.fake.script_stk(0, receipt="QAB1CD2EF3")
        tx = self.stk(150)
        self.deliver()
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.receipt, "QAB1CD2EF3")
        self.assertEqual(tx.phone, "254708374149")
        self.assertTrue(tx.trans_time)
        line = tx.statement_line_id
        self.assertTrue(line)
        self.assertEqual(line.unique_import_id, "MPESA-QAB1CD2EF3")
        self.assertEqual(line.amount, 150)
        self.assertEqual(line.journal_id, self.till.journal_id)
        self.assertTrue(line.statement_id, "lines are grouped in a daily statement")
        self.assertTrue(line.statement_id.is_complete, "and the statement stays complete")

    def test_every_failure_code(self):
        for code in (1032, 1037, 1, 2001, 1019, 1001, 17, 2028):
            self.fake.script_stk(code)
            tx = self.stk(10 + code % 50, idempotency_key=f"code-{code}")
            self.deliver()
            self.assertEqual(tx.state, "failed", code)
            self.assertEqual(tx.result_code, str(code))
            self.assertEqual(tx.cashier_message, daraja.stk_message(str(code)))
            self.assertFalse(tx.statement_line_id)

    def test_duplicate_callback(self):
        self.fake.script_stk(0, receipt="QAB1CD2EF4")
        tx = self.stk(100)
        self.deliver(duplicate=[0])
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(self.env["account.bank.statement.line"].search_count(
            [("unique_import_id", "=", "MPESA-QAB1CD2EF4")]), 1)
        callbacks = self.env["mpesa.callback"].search([("dedup_key", "like", tx.checkout_request_id)])
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(callbacks.mapped("duplicate").count(True), 1)

    def test_callback_before_our_response_is_saved(self):
        """Daraja answers, but its callback lands before we store the ids."""
        self.fake.script_stk(0, receipt="QAB1CD2EF5")
        Tx = self.env["mpesa.transaction"]
        original = Tx.__class__._stk_save_ids

        def late_save(record, merchant_id, checkout_id):
            # The callback is processed first...
            self.deliver()
            # ...then our own save.
            return original(record, merchant_id, checkout_id)

        with patch.object(Tx.__class__, "_stk_save_ids", late_save):
            tx = self.stk(120)
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.receipt, "QAB1CD2EF5")
        self.assertEqual(self.transactions(checkout_request_id=tx.checkout_request_id), tx,
                         "the placeholder was merged into the request")
        self.assertEqual(tx.statement_line_id.mpesa_transaction_id, tx)

    def test_no_callback_then_stk_query(self):
        self.fake.script_stk(1032)
        tx = self.stk(90)
        self.fake.take()  # the callback is lost
        with freeze_time(fields.Datetime.now() + timedelta(seconds=120)):
            self.env["mpesa.transaction"]._cron_stk_followup()
        self.assertEqual(tx.state, "failed")
        self.assertEqual(tx.result_code, "1032")
        self.assertEqual(tx.query_count, 1)

    def test_stk_query_pending_then_paid_waits_for_receipt(self):
        self.fake.script_stk(0, receipt="QAB1CD2EF6", query="pending")
        tx = self.stk(250)
        lost = self.fake.take()
        tx._stk_query()
        self.assertEqual(tx.state, "requested", "still processing")
        # Daraja finishes; STK Query says paid but carries no receipt.
        self.fake.stk[tx.checkout_request_id]["script"]["query"] = "final"
        tx._stk_query()
        self.assertEqual(tx.state, "confirmed")
        self.assertTrue(tx.awaiting_receipt)
        self.assertFalse(tx.statement_line_id)
        # The till's C2B confirmation brings the receipt.
        self.fake.c2b_payment("600100", 250, receipt="QAB1CD2EF6")
        self.deliver()
        self.assertEqual(tx.receipt, "QAB1CD2EF6")
        self.assertFalse(tx.awaiting_receipt)
        self.assertTrue(tx.statement_line_id)
        self.assertEqual(self.transactions(receipt="QAB1CD2EF6"), tx)
        self.assertTrue(lost)

    def test_reset_after_send_is_unknown_not_retried(self):
        self.fake.drop_after_send.add("/mpesa/stkpush/v1/processrequest")
        tx = self.stk(300)
        self.assertEqual(tx.state, "unknown")
        self.assertEqual(len(self.sent()), 1, "never sent twice")
        # The customer paid anyway: the callback finds its request by phone and amount.
        self.deliver()
        self.assertEqual(tx.state, "confirmed")
        self.assertTrue(tx.checkout_request_id)
        self.assertTrue(tx.statement_line_id)

    def test_connection_refused_is_a_clean_failure(self):
        self.app.api_url = "http://127.0.0.1:9"
        tx = self.stk(300)
        self.assertEqual(tx.state, "failed")
        self.assertIn("nothing was sent", tx.cashier_message)

    def test_idempotency(self):
        first = self.stk(400, idempotency_key="pos-line-1")
        second = self.stk(400, idempotency_key="pos-line-1")
        self.assertEqual(first, second)
        self.assertEqual(len(self.sent()), 1, "the customer is prompted once")

    def test_failed_request_can_be_retried_with_the_same_key(self):
        self.fake.script_stk(1032)
        first = self.stk(400, idempotency_key="pos-line-2")
        self.deliver()
        second = self.stk(400, idempotency_key="pos-line-2")
        self.assertNotEqual(first, second)
        self.assertEqual(second.idempotency_key, "pos-line-2")

    def test_threshold_callback_is_confirmed_by_stk_query(self):
        self.till.stk_confirm_threshold = 1000
        self.fake.script_stk(0, receipt="QAB1CD2EF7", query="pending")
        tx = self.stk(5000)
        self.deliver()
        self.assertEqual(tx.state, "requested", "a callback alone is not believed")
        self.assertTrue(tx.pending_callback)
        self.fake.stk[tx.checkout_request_id]["script"]["query"] = "final"
        tx._stk_query()
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.receipt, "QAB1CD2EF7")

    def test_forged_callback_is_caught_by_stk_query(self):
        self.till.stk_confirm_threshold = 1000
        self.fake.script_stk(1032)
        tx = self.stk(5000)
        self.fake.take()
        forged = {"Body": {"stkCallback": {
            "MerchantRequestID": tx.merchant_request_id,
            "CheckoutRequestID": tx.checkout_request_id,
            "ResultCode": 0, "ResultDesc": "ok",
            "CallbackMetadata": {"Item": [
                {"Name": "Amount", "Value": 5000},
                {"Name": "MpesaReceiptNumber", "Value": "FAKE123456"},
                {"Name": "TransactionDate", "Value": 20261024120000},
                {"Name": "PhoneNumber", "Value": 254708374149},
            ]}}}}
        self.send(self.till.sudo()._callback_url("stk"), forged, ip="10.0.0.1")
        self.assertEqual(tx.state, "failed")
        self.assertEqual(tx.exception, "unverified_callback")
        self.assertFalse(tx.statement_line_id)

    def test_amount_paid_differs(self):
        self.fake.script_stk(0, amount=90)
        tx = self.stk(100)
        self.deliver()
        self.assertEqual(tx.amount, 90)
        self.assertEqual(tx.exception, "amount_mismatch")

    def test_invoice_wizard(self):
        invoice = self.invoice(1234.40)
        self.assertTrue(invoice.mpesa_can_request)
        wizard = self.env["mpesa.stk.request"].with_context(
            default_move_id=invoice.id).create({"shortcode_id": self.paybill.id})
        self.assertEqual(wizard.amount, 1234)
        self.assertEqual(wizard.phone, PHONE)
        wizard.action_send()
        tx = wizard.transaction_id
        self.assertEqual(tx.move_id, invoice)
        self.assertEqual(tx.state, "requested")
        with self.assertRaises(UserError):
            wizard.copy({"amount": 5000}).action_send()
