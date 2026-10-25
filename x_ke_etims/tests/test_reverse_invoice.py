# -*- coding: utf-8 -*-
"""Reverse invoices: our eTIMS invoice for a farmer's sale to us, via DigiTax."""

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .digitax_fixtures import SALE_SIGNED
from .test_digitax import DigiTaxCase


@tagged("post_install", "-at_install")
class TestReverseInvoice(DigiTaxCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.farmer = cls.env["res.partner"].create({
            "name": "Jane Wambui (maize farmer)", "country_id": cls.env.ref("base.ke").id})
        cls.maize = cls.make_product("Dry maize, 90kg bag", 4500.0, cls.ke_tax("ST0"))

    def setUp(self):
        super().setUp()
        self.fake.sale_outcome = "COMPLETED"

    def farmer_bill(self, partner=None, taxes=None, post=True, reverse=True):
        bill = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": (partner or self.farmer).id,
            "company_id": self.ke_company.id,
            "invoice_date": fields.Date.today(),
            "ref": "Weighbridge 0042",
            "ke_etims_reverse_invoice": reverse,
            "invoice_line_ids": [Command.create({
                "product_id": self.maize.id, "quantity": 10, "price_unit": 4500.0,
                "tax_ids": [Command.set((taxes or self.env["account.tax"]).ids)]})],
        })
        if post:
            bill.action_post()
        return bill

    def test_a_reverse_invoice_is_signed_and_backs_the_bill(self):
        bill = self.farmer_bill()
        self.assertTrue(bill.ke_etims_can_reverse_invoice)
        self.assertTrue(bill.ke_etims_applicable)
        self.assertEqual(bill.ke_etims_state, "to_send")
        self.run_queue()

        document = bill.ke_etims_document_id
        self.assertEqual(document.kind, "reverse_invoice")
        self.assertEqual(document.state, "sent", document.last_error)
        body = self.calls("POST", "/reverse-invoices")[0][2]
        self.assertEqual(body["supplier_name"], "Jane Wambui (maize farmer)")
        self.assertIsNone(body["supplier_pin"])
        self.assertEqual(body["invoice_number"], str(document.kra_invoice_no))
        self.assertEqual(body["total_amount"], 45000.0)
        self.assertEqual(body["total_tax_amount"], 0)
        item = body["items"][0]
        self.assertEqual(item["tax_type"], "D", "No VAT: the seller is not registered.")
        self.assertEqual(item["item_id"], "itm_1")
        self.assertEqual(bill.ke_etims_supplier_invoice_ref, "KRACU0100000001/%s"
                         % SALE_SIGNED["receipt_number"])
        self.assertEqual(bill.ke_etims_cu_invoice, bill.ke_etims_supplier_invoice_ref)
        with self.assertRaises(UserError):
            bill.button_draft()

    def test_a_vat_line_keeps_its_tax_type(self):
        bill = self.farmer_bill(taxes=self.ke_tax("PT16"))
        self.run_queue()
        self.assertEqual(bill.ke_etims_state, "sent", bill.ke_etims_last_error)
        item = self.calls("POST", "/reverse-invoices")[0][2]["items"][0]
        self.assertEqual(item["tax_type"], "B")
        self.assertEqual(item["total_amount"], 52200.0)
        self.assertEqual(item["tax_amount"], 7200.0)

    def test_a_pending_reverse_invoice_is_polled_where_it_lives(self):
        self.fake.sale_outcome = "PENDING"
        bill = self.farmer_bill()
        self.run_queue()
        document = bill.ke_etims_document_id
        self.assertEqual(document.state, "pending")
        self.assertEqual(document.provider_ref, "rev_1")
        self.fake.complete("rev_1")
        callback = {"event": "reverse_invoice.completed", "data": {"id": "rev_1"}}
        self.env["ke.etims.document"]._ke_etims_handle_callback(self.dt_device, callback)
        self.assertEqual(document.state, "sent")
        self.assertTrue(self.calls("GET", "/reverse-invoices/rev_1"))
        self.assertFalse(self.calls("GET", "/sales/rev_1"))

    def test_an_ordinary_bill_is_not_sent(self):
        bill = self.farmer_bill(reverse=False)
        self.assertFalse(bill.ke_etims_applicable)
        self.assertFalse(bill.ke_etims_state)
        self.assertFalse(bill.ke_etims_document_ids)

    def test_a_seller_abroad_is_refused(self):
        bill = self.farmer_bill(partner=self.env["res.partner"].create({
            "name": "Kampala Grain", "country_id": self.env.ref("base.ug").id}))
        self.run_queue()
        self.assertEqual(bill.ke_etims_state, "failed")
        self.assertIn("seller in Kenya", bill.ke_etims_last_error)

    def test_a_backend_without_reverse_invoices_refuses_it(self):
        self.dt_device.active = False
        self.dt_device.flush_recordset(["active"])
        self.device.active = True
        bill = self.farmer_bill(post=False, reverse=False)
        self.assertFalse(bill.ke_etims_can_reverse_invoice, "Demo has no reverse invoices.")
        bill.ke_etims_reverse_invoice = True
        bill.action_post()
        self.run_queue()
        self.assertEqual(bill.ke_etims_state, "failed")
        self.assertIn("cannot issue reverse invoices", bill.ke_etims_last_error)
        self.assertFalse(bill.ke_etims_document_id.kra_invoice_no, "No number was drawn.")
