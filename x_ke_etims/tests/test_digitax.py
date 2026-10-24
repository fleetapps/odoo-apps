# -*- coding: utf-8 -*-
"""DigiTax: items first, sales that finish later, callbacks never believed."""

import json
from unittest.mock import patch

import requests

from odoo.tests import HttpCase, tagged

from .common import KeEtimsCase
from .digitax_fixtures import SALE_SIGNED, FakeDigiTax


class DigiTaxCase(KeEtimsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.device.active = False
        cls.fake = FakeDigiTax()
        cls.startClassPatcher(patch.object(requests.Session, "request", autospec=True,
                                           side_effect=cls.fake))
        cls.dt_device = cls.make_device(cls.ke_company, backend="digitax", environment="test",
                                        api_key="test-key")
        cls.dt_device.action_initialize()

    def setUp(self):
        super().setUp()
        self.fake.calls.clear()
        self.fake.sales.clear()
        self.fake.items.clear()
        self.fake.sale_outcome = "PENDING"

    def calls(self, method, path):
        return [call for call in self.fake.calls if call[0] == method and call[1] == path]


@tagged("post_install", "-at_install")
class TestDigiTax(DigiTaxCase):

    def test_initialising_reads_the_business_and_its_scu(self):
        self.assertEqual(self.dt_device.state, "initialised", self.dt_device.last_error)
        self.assertEqual(self.dt_device.scu_id, "KRACU0100000001")
        self.assertIn("/x_ke_etims/digitax/%s/" % self.dt_device.id,
                      self.dt_device.sudo().callback_url)

    def test_items_are_created_once_then_updated(self):
        template = self.product.product_tmpl_id
        registration = template._ke_etims_register(self.dt_device)
        self.assertEqual(registration.provider_item_id, "itm_1")
        created = self.calls("POST", "/items")[0]
        self.assertEqual(created[2]["item_class_code"], "5059690800")
        self.assertEqual(created[2]["tax_type"], "B")
        self.assertEqual(created[3]["X-API-Key"], "test-key")
        self.assertIn(self.dt_device.sudo().callback_token, created[2]["callback_url"])
        template._ke_etims_register(self.dt_device)
        self.assertTrue(self.calls("PUT", "/items/itm_1"))
        self.assertEqual(len(self.calls("POST", "/items")), 1)

    def test_a_pending_sale_is_polled_to_completion(self):
        invoice = self.ke_invoice()
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "pending", document.last_error)
        self.assertEqual(document.provider_ref, "sal_1")
        sent = self.calls("POST", "/sales")[0][2]
        self.assertEqual(sent["items"][0]["item_id"], "itm_1")
        self.assertEqual(sent["invoice_number"], invoice.name)
        self.assertEqual(sent["total_amount"], invoice.amount_total)
        # Still pending at DigiTax: polling changes nothing.
        self.run_queue()
        self.assertEqual(document.state, "pending")
        self.fake.complete("sal_1")
        self.run_queue()
        self.assertEqual(document.state, "sent")
        self.assertEqual(invoice.ke_etims_signature, SALE_SIGNED["receipt_signature"])
        self.assertEqual(invoice.ke_etims_receipt_no, 14)
        self.assertEqual(invoice.ke_etims_qr_url, SALE_SIGNED["etims_url"])
        self.assertEqual(invoice.ke_etims_scu_id, "KRACU0100000001")

    def test_a_callback_is_checked_with_digitax_before_anything_changes(self):
        invoice = self.ke_invoice()
        self.run_queue()
        document = invoice.ke_etims_document_id
        forged = {"event": "sale.sync",
                  "data": {"id": "sal_1", "status": "COMPLETED", "receipt_signature": "FORGED"}}
        # DigiTax still says pending, whatever the notification claims.
        self.env["ke.etims.document"]._ke_etims_handle_callback(self.dt_device, forged)
        self.assertEqual(document.state, "pending")
        self.assertTrue(self.calls("GET", "/sales/sal_1"))
        self.fake.complete("sal_1")
        self.env["ke.etims.document"]._ke_etims_handle_callback(self.dt_device, forged)
        self.assertEqual(document.state, "sent")
        self.assertEqual(document.signature, SALE_SIGNED["receipt_signature"])

    def test_a_failed_sale_fails_the_document(self):
        self.fake.sale_outcome = "FAILED"
        invoice = self.ke_invoice()
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "failed")
        self.assertIn("Item not synced", document.last_error)

    def test_a_refused_key_says_so(self):
        self.dt_device.sudo().api_key = "wrong"
        invoice = self.ke_invoice()
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "failed")
        self.assertIn("refused the API key", document.last_error)
        self.assertNotIn("wrong", document.last_error.replace("refused", ""))

    def test_a_credit_note_names_the_original_sale(self):
        self.fake.sale_outcome = "COMPLETED"
        invoice = self.ke_invoice()
        self.run_queue()
        self.assertEqual(invoice.ke_etims_state, "sent")
        refund = self.credit(invoice, reason="01")
        self.run_queue()
        body = self.calls("POST", "/credit-notes")[0][2]
        self.assertEqual(body["original_sale_id"], "sal_1")
        self.assertEqual(body["reason_code"], "01")
        self.assertEqual(refund.ke_etims_state, "sent")

    def test_a_network_error_after_sending_is_retried(self):
        self.fake.sale_outcome = "COMPLETED"
        invoice = self.ke_invoice()
        template = self.product.product_tmpl_id
        template._ke_etims_register(self.dt_device)
        self.fake.fail[("POST", "/sales")] = (None, None)
        self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertEqual(document.state, "to_send")
        self.assertTrue(document.may_have_been_processed)
        self.run_queue()
        self.assertEqual(document.state, "sent")

    def test_branches_and_notices(self):
        self.dt_device.action_fetch_branches()
        branches = self.env["ke.etims.branch"].search([("company_id", "=", self.ke_company.id)])
        self.assertEqual(set(branches.mapped("bhf_id")), {"00", "01"})
        self.assertEqual(branches.filtered("is_head_office").bhf_id, "00")
        self.assertTrue(self.env["ke.etims.notice"].search([("name", "=", "System maintenance")]))

    def test_no_code_sync_is_offered_and_the_seed_suffices(self):
        self.assertFalse(self.dt_device.can_sync_codes)
        self.assertEqual(self.dt_device._ke_etims_tax_rates()["B"], 16.0)


@tagged("post_install", "-at_install")
class TestDigiTaxCallbackRoute(DigiTaxCase, HttpCase):

    def post(self, device_id, token, payload):
        return self.url_open(
            "/x_ke_etims/digitax/%s/%s" % (device_id, token),
            data=json.dumps(payload), headers={"Content-Type": "application/json"})

    def test_a_wrong_token_is_not_found(self):
        response = self.post(self.dt_device.id, "guess", {"event": "sale.sync", "id": "x"})
        self.assertEqual(response.status_code, 404)

    def test_a_good_token_triggers_a_refetch(self):
        self.fake.sale_outcome = "COMPLETED"
        invoice = self.ke_invoice()
        document = invoice.ke_etims_document_id
        template = self.product.product_tmpl_id
        template._ke_etims_register(self.dt_device)
        document.write({"state": "pending", "provider_ref": "sal_1"})
        self.fake.sales["sal_1"] = {"request": {}, "status": "COMPLETED"}
        response = self.post(self.dt_device.id, self.dt_device.sudo().callback_token,
                             {"event": "sale.sync", "data": {"id": "sal_1"}})
        self.assertEqual(response.status_code, 200, response.text)
        document.invalidate_recordset()
        self.assertEqual(document.state, "sent")
