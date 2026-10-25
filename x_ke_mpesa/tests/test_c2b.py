# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""C2B confirmations: the IPN on every till and paybill."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MpesaCommon


@tagged("post_install", "-at_install")
class TestC2B(MpesaCommon):

    def test_registration(self):
        urls = self.fake.c2b_urls["600100"]
        self.assertTrue(urls["confirmation"].startswith("https://pay.example.co.ke/pay/ke/"))
        self.assertTrue(urls["confirmation"].endswith("/c2b/confirm"))
        for word in ("mpesa", "safaricom", "exe", "cmd", "sql", "query"):
            self.assertNotIn(word, urls["confirmation"].lower())
        self.assertTrue(self.till.c2b_registered_at)

    def test_production_registration_is_one_time(self):
        self.fake.production = True
        self.till.c2b_registered_at = False
        with self.assertRaisesRegex(UserError, "one-time"):
            self.till.action_register_c2b()

    def test_forbidden_words_in_the_host(self):
        problems = self.till._url_problems("https://mpesa.example.co.ke/pay/ke/abc/c2b/confirm")
        self.assertTrue(any("mpesa" in p for p in problems))
        problems = self.till._url_problems("http://localhost:8069/pay/ke/abc/c2b/confirm")
        self.assertTrue(any("HTTPS" in p for p in problems))

    def test_confirmation(self):
        receipt = self.fake.c2b_payment("600100", 500, first_name="AMINA", org_balance=1500.0)
        answers = []
        self.fake.deliver(lambda url, body: answers.append(self.send(url, body)))
        self.assertEqual(answers, [{"ResultCode": 0, "ResultDesc": "Accepted"}])
        tx = self.transactions(receipt=receipt)
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.source, "c2b")
        self.assertEqual(tx.channel, "till")
        self.assertEqual(tx.amount, 500)
        self.assertEqual(tx.customer_name, "AMINA")
        self.assertEqual(tx.msisdn_masked, "2547*****149")
        self.assertFalse(tx.phone, "a C2B number is masked: never a full phone")
        self.assertEqual(tx.org_balance, 1500)
        self.assertEqual(self.till.last_org_balance, 1500)
        self.assertTrue(self.till.last_callback_at)
        self.assertEqual(tx.statement_line_id.unique_import_id, f"MPESA-{receipt}")
        self.assertEqual(tx.allocation_state, "unallocated")

    def test_balance_check(self):
        self.fake.c2b_payment("600100", 500, org_balance=500.0)
        self.deliver()
        self.env["mpesa.transaction"]._cron_balance_check()
        self.assertFalse(self.till.message_ids.filtered(
            lambda m: "Balance check" in (m.body or "")), "Odoo agrees with M-PESA")
        # M-PESA says 1,700 after a receipt of 700: 500 went missing somewhere.
        self.fake.c2b_payment("600100", 700, org_balance=1700.0)
        self.deliver()
        self.env["mpesa.transaction"]._cron_balance_check()
        message = self.till.message_ids.filtered(lambda m: "Balance check" in (m.body or ""))
        self.assertTrue(message)
        self.assertIn("500", message[0].body)

    def test_duplicate_and_reordered_confirmations(self):
        first = self.fake.c2b_payment("600100", 100)
        second = self.fake.c2b_payment("600100", 200)
        self.deliver(duplicate=[0, 1], reverse=True)
        for receipt in (first, second):
            self.assertEqual(len(self.transactions(receipt=receipt)), 1)
            self.assertEqual(self.env["account.bank.statement.line"].search_count(
                [("unique_import_id", "=", f"MPESA-{receipt}")]), 1)

    def test_dropped_confirmation_is_simply_absent(self):
        kept = self.fake.c2b_payment("600100", 100)
        lost = self.fake.c2b_payment("600100", 200)
        self.deliver(drop=[1])
        self.assertTrue(self.transactions(receipt=kept))
        self.assertFalse(self.transactions(receipt=lost))

    def test_store_registration_routes_to_the_till_it_names(self):
        self.store.action_register_c2b()
        receipt = self.fake.c2b_payment("174379", 700, business_shortcode="600101")
        self.deliver()
        self.assertEqual(self.transactions(receipt=receipt).shortcode_id, self.till_b)

    def test_store_registration_naming_only_the_store(self):
        self.store.action_register_c2b()
        receipt = self.fake.c2b_payment("174379", 700)
        self.deliver()
        self.assertEqual(self.transactions(receipt=receipt).shortcode_id, self.store,
                         "it stays on the store until a cashier picks it")

    def test_paybill_confirmation(self):
        receipt = self.fake.c2b_payment("600200", 800, bill_ref="NOTHING-KNOWN")
        self.deliver()
        tx = self.transactions(receipt=receipt)
        self.assertEqual(tx.channel, "paybill")
        self.assertEqual(tx.bill_ref, "NOTHING-KNOWN")
        self.assertEqual(tx.allocation_state, "unallocated")

    def test_unknown_token(self):
        answer = self.env["mpesa.callback"].sudo()._receive(
            "c2b_confirm", "0" * 32, b'{"TransID": "QAB1CD2EF9"}', "196.201.214.10")
        self.assertIsNone(answer)

    def test_allowlist(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("x_ke_mpesa.ip_allowlist_enabled", "True")
        params.set_param("x_ke_mpesa.ip_allowlist", "196.201.212.0/24,196.201.214.0/24")
        receipt = self.fake.c2b_payment("600100", 100)
        items = self.fake.take()
        answer = self.send(items[0]["url"], items[0]["body"], ip="41.90.1.1")
        self.assertEqual(answer["ResultCode"], 0, "never tell a stranger anything")
        self.assertFalse(self.transactions(receipt=receipt))
        callback = self.env["mpesa.callback"].search([("dedup_key", "=", receipt)])
        self.assertEqual(callback.state, "ignored")
        self.assertFalse(callback.ip_trusted)
        callback.action_process()
        self.assertTrue(self.transactions(receipt=receipt), "an officer can release it")

    def test_validation_accepts(self):
        answer = self.env["mpesa.callback"].sudo()._receive(
            "c2b_validate", self.till.sudo().callback_token,
            b'{"TransID": "QAB1CD2EF8", "TransAmount": "10"}', "196.201.214.10")
        self.assertEqual(answer, {"ResultCode": "0", "ResultDesc": "Accepted"})

    def test_garbage_body(self):
        answer = self.env["mpesa.callback"].sudo()._receive(
            "c2b_confirm", self.till.sudo().callback_token, b"not json", "196.201.214.10")
        self.assertEqual(answer["ResultCode"], 0)
        callback = self.env["mpesa.callback"].search([], limit=1)
        self.assertEqual(callback.state, "error")

    def test_transaction_status_confirms_known_receipt(self):
        self.till.sudo().write({"initiator_name": "apiop", "security_credential": "cred"})
        receipt = self.fake.c2b_payment("600100", 640)
        self.deliver()
        tx = self.transactions(receipt=receipt)
        tx._request_transaction_status()
        self.deliver()
        callback = self.env["mpesa.callback"].search([("kind", "=", "status_result")], limit=1)
        self.assertEqual(callback.state, "done")
        self.assertEqual(callback.transaction_id, tx)
