# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The Daraja rules that need no database."""

import base64
from datetime import UTC, datetime

import requests
import urllib3

from odoo.tests import TransactionCase, tagged

from ..lib import daraja
from ..lib.log_filter import mask_tokens


@tagged("post_install", "-at_install")
class TestDarajaRules(TransactionCase):

    def test_stk_password(self):
        # Daraja's own sandbox example: shortcode 174379, the public passkey,
        # timestamp 20160216165627.
        password = daraja.stk_password(
            "174379", "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919",
            "20160216165627")
        self.assertEqual(base64.b64decode(password).decode(),
                         "174379bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72"
                         "ada1ed2c91920160216165627")

    def test_timestamp_is_nairobi_time(self):
        moment = datetime(2026, 10, 24, 21, 30, 5, tzinfo=UTC)
        self.assertEqual(daraja.daraja_timestamp(moment), "20261025003005")

    def test_normalize_msisdn(self):
        for raw, expected in [
            ("0708374149", "254708374149"),
            ("0708 374 149", "254708374149"),
            ("708374149", "254708374149"),
            ("+254 708 374149", "254708374149"),
            ("254708374149", "254708374149"),
            ("0110123456", "254110123456"),
            ("110123456", "254110123456"),
            ("254110123456", "254110123456"),
        ]:
            self.assertEqual(daraja.normalize_msisdn(raw), expected, raw)
        for bad in ("", "12345", "0208374149", "+44 7700 900123", "25420123456", None):
            with self.assertRaises(ValueError, msg=bad):
                daraja.normalize_msisdn(bad)

    def test_mask_msisdn(self):
        self.assertEqual(daraja.mask_msisdn("254708374149"), "2547*****149")
        self.assertEqual(daraja.mask_msisdn(""), "")

    def test_whole_shillings(self):
        self.assertEqual(daraja.whole_shillings(1234.4), 1234)
        self.assertEqual(daraja.whole_shillings(1234.5), 1235)
        self.assertEqual(daraja.whole_shillings("99.50"), 100)
        self.assertEqual(daraja.whole_shillings(1), 1)
        self.assertIsInstance(daraja.whole_shillings(10.0), int)
        with self.assertRaises(ValueError):
            daraja.whole_shillings("ten")

    def test_limits(self):
        self.assertEqual(daraja.account_reference("INV/2026/00012-A"), "INV202600012")
        self.assertEqual(daraja.account_reference(""), "PAYMENT")
        self.assertEqual(daraja.transaction_desc("Invoice INV/2026/00012"), "Invoice INV20")
        self.assertLessEqual(len(daraja.transaction_desc("x" * 50)), 13)

    def test_parse_times(self):
        expected = datetime(2019, 12, 19, 7, 21, 15, tzinfo=UTC)
        self.assertEqual(daraja.parse_daraja_time(20191219102115), expected)
        self.assertEqual(daraja.parse_daraja_time("20191219102115"), expected)
        self.assertEqual(daraja.parse_daraja_time("2019-12-19 10:21:15"), expected)
        self.assertEqual(daraja.parse_daraja_time("2019-12-19T07:21:15Z"), expected)
        self.assertIsNone(daraja.parse_daraja_time(""))
        self.assertIsNone(daraja.parse_daraja_time("yesterday"))

    def test_amounts(self):
        self.assertEqual(daraja.parse_amount("5.00"), 5.0)
        self.assertEqual(daraja.parse_amount(10), 10.0)
        self.assertEqual(daraja.parse_amount("1,500"), 1500.0)
        self.assertEqual(daraja.parse_amount(""), 0.0)

    def test_stk_metadata_and_failures(self):
        success = {"CallbackMetadata": {"Item": [
            {"Name": "Amount", "Value": 1.0},
            {"Name": "MpesaReceiptNumber", "Value": "NLJ7RT61SV"},
            {"Name": "Balance"},
        ]}}
        self.assertEqual(daraja.stk_metadata(success)["MpesaReceiptNumber"], "NLJ7RT61SV")
        self.assertIsNone(daraja.stk_metadata(success)["Balance"])
        self.assertEqual(daraja.stk_metadata({"ResultCode": 1032}), {})

    def test_every_result_code_has_a_plain_message(self):
        for code in ("0", "1", "17", "1001", "1019", "1032", "1037", "2001", "2028", "8006"):
            self.assertNotIn("M-PESA result", daraja.stk_message(code), code)
        self.assertEqual(daraja.stk_message("9999", "Something"), "Something")

    def test_redact(self):
        clean = daraja.redact({"Password": "secret", "SecurityCredential": "x",
                               "PhoneNumber": "254708374149", "Amount": 10,
                               "nested": [{"Authorization": "Bearer t"}]})
        self.assertEqual(clean["Password"], "***")
        self.assertEqual(clean["SecurityCredential"], "***")
        self.assertEqual(clean["PhoneNumber"], "2547*****149")
        self.assertEqual(clean["nested"][0]["Authorization"], "***")
        self.assertEqual(clean["Amount"], 10)

    def test_post_is_never_retried_on_read_errors(self):
        """Fleet's production lesson: Daraja has no idempotency key, and a read
        error on the STK POST happens after Safaricom got it."""
        retries = daraja.SESSION.get_adapter("https://api.safaricom.co.ke").max_retries
        self.assertIn("GET", retries.allowed_methods)
        self.assertNotIn("POST", retries.allowed_methods)
        self.assertGreater(retries.backoff_factor, 0)
        for status in (429, 500, 502, 503, 504):
            self.assertIn(status, retries.status_forcelist)

    def test_sent_or_not(self):
        never = requests.exceptions.ConnectionError(urllib3.exceptions.MaxRetryError(
            None, "/", urllib3.exceptions.NewConnectionError(None, "refused")))
        self.assertFalse(daraja.was_sent(never))
        reset = requests.exceptions.ConnectionError(
            urllib3.exceptions.ProtocolError("Connection aborted.", ConnectionResetError()))
        self.assertTrue(daraja.was_sent(reset))
        self.assertFalse(daraja.was_sent(requests.exceptions.ConnectTimeout()))

    def test_callback_tokens_never_reach_the_log(self):
        line = '196.201.214.10 - "POST /pay/ke/0123456789abcdef0123456789abcdef/c2b/confirm HTTP/1.1" 200'
        masked = mask_tokens(line)
        self.assertNotIn("0123456789abcdef0123456789abcdef", masked)
        self.assertIn("/pay/ke/…cdef/c2b/confirm", masked)
