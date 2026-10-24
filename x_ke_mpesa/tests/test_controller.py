# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The public endpoints, over real HTTP."""

import json

from odoo.tests import HttpCase, tagged

from .fake_daraja import nairobi_now


@tagged("post_install", "-at_install")
class TestCallbackEndpoint(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        company = cls.env.company
        journal = cls.env["account.journal"].create({
            "name": "M-Pesa test", "type": "bank", "code": "MPT1"})
        cls.till = cls.env["mpesa.shortcode"].create({
            "kind": "till", "number": "600900", "company_id": company.id,
            "journal_id": journal.id,
        })

    def post(self, path, body):
        return self.url_open(path, data=json.dumps(body),
                             headers={"Content-Type": "application/json"})

    def test_confirmation_over_http(self):
        token = self.till.sudo().callback_token
        response = self.post(f"/pay/ke/{token}/c2b/confirm", {
            "TransactionType": "Buy Goods", "TransID": "QHTTP00001",
            "TransTime": nairobi_now(), "TransAmount": "10.00",
            "BusinessShortCode": "600900", "BillRefNumber": "", "InvoiceNumber": "",
            "OrgAccountBalance": "10.00", "ThirdPartyTransID": "",
            "MSISDN": "2547 ***** 149", "FirstName": "JOHN", "MiddleName": "", "LastName": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ResultCode": 0, "ResultDesc": "Accepted"})
        callback = self.env["mpesa.callback"].search([("dedup_key", "=", "QHTTP00001")])
        self.assertTrue(callback)
        self.assertTrue(callback.remote_ip)
        # The request only stored the body and queued the work.
        self.assertEqual(callback.state, "new")
        job = self.env["queue.job"].search([("model_name", "=", "mpesa.callback"),
                                            ("method_name", "=", "_process")])
        self.assertEqual(job.records, callback)
        callback._process()
        tx = self.env["mpesa.transaction"].search([("receipt", "=", "QHTTP00001")])
        self.assertEqual(tx.amount, 10)
        self.assertEqual(callback.state, "done")

    def test_unknown_token_is_404(self):
        response = self.post(f"/pay/ke/{'0' * 32}/c2b/confirm", {"TransID": "X"})
        self.assertEqual(response.status_code, 404)

    def test_unknown_path_is_404(self):
        token = self.till.sudo().callback_token
        response = self.post(f"/pay/ke/{token}/something/else", {})
        self.assertEqual(response.status_code, 404)

    def test_get_is_refused(self):
        token = self.till.sudo().callback_token
        response = self.url_open(f"/pay/ke/{token}/c2b/confirm")
        self.assertNotEqual(response.status_code, 200)
