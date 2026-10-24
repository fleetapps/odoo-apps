# -*- coding: utf-8 -*-
"""What the invoice PDF prints, and who may see a device's secrets."""

import re

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..backends import EtimsError
from ..backends.base import redact
from .common import KeEtimsCase


@tagged("post_install", "-at_install")
class TestReport(KeEtimsCase):

    def render(self, invoice):
        """The PDF's HTML as text, tags out and whitespace collapsed."""
        html = self.env["ir.actions.report"]._render_qweb_html(
            "account.report_invoice", invoice.ids)[0].decode()
        self.html = html
        return " ".join(re.sub(r"<[^>]+>", " ", html).split())

    def test_a_signed_invoice_prints_its_scu_information_and_qr(self):
        invoice = self.ke_invoice()
        self.run_queue()
        html = self.render(invoice)
        document = invoice.ke_etims_document_id
        self.assertIn("SCU INFORMATION", html)
        self.assertIn("CU ID: %s" % document.scu_id, html)
        self.assertIn("%s/%s" % (document.scu_id, document.receipt_no), html)
        # Signature and internal data, dash after every fourth character.
        self.assertIn(document.signature[:4] + "-" + document.signature[4:8], html)
        self.assertIn("data:image/png;base64,", self.html)
        self.assertIn("B-16.00%", html)
        self.assertIn("DEMO - NOT A TAX INVOICE", html, "Signed by the demo backend.")

    def test_an_unsigned_invoice_says_it_is_not_a_tax_invoice(self):
        invoice = self.ke_invoice()
        html = self.render(invoice)
        self.assertNotIn("SCU INFORMATION", html)
        self.assertIn("DEMO - NOT A TAX INVOICE", html)
        self.device.active = False
        other = self.make_device(self.ke_company, backend="digitax", environment="test",
                                 api_key="test-key")
        other.state = "initialised"
        invoice.ke_etims_document_id.device_id = other
        self.assertIn("Not yet signed by KRA eTIMS", self.render(invoice))

    def test_a_credit_note_prints_the_original_cu_invoice(self):
        invoice = self.ke_invoice()
        self.run_queue()
        refund = self.credit(invoice)
        self.run_queue()
        original = invoice.ke_etims_document_id
        self.assertIn("Original CU Invoice No.#: %s/%s" % (original.scu_id, original.receipt_no),
                      self.render(refund))


@tagged("post_install", "-at_install")
class TestSecurity(KeEtimsCase):

    def test_an_accountant_sees_statuses_not_secrets(self):
        self.device.sudo().write({"api_key": "never-show-me"})
        invoice = self.ke_invoice()
        user = self.salesperson
        self.assertFalse(user.has_group("x_ke_etims.group_ke_etims_manager"))
        device = self.device.with_user(user)
        self.assertTrue(device.name)
        with self.assertRaises(AccessError):
            device.cmc_key  # noqa: B018
        with self.assertRaises(AccessError):
            self.env["ke.etims.log"].with_user(user).search([])
        # ... yet can send an invoice, which reads the key on the server.
        invoice.with_user(user).action_ke_etims_send()
        self.assertEqual(invoice.ke_etims_state, "sent")

    def test_logs_never_hold_a_key(self):
        self.device.sudo().write({"cmc_key": "SECRET-CMC-KEY-1234"})
        cleaned = redact({"tin": "P051234567X", "cmcKey": "SECRET-CMC-KEY-1234",
                          "headers": {"X-API-Key": "abc"},
                          "message": "key SECRET-CMC-KEY-1234 refused"},
                         secrets=["SECRET-CMC-KEY-1234"])
        self.assertEqual(cleaned["cmcKey"], "***")
        self.assertEqual(cleaned["headers"]["X-API-Key"], "***")
        self.assertNotIn("SECRET-CMC-KEY-1234", cleaned["message"])
        self.assertEqual(cleaned["tin"], "P051234567X")

    def test_errors_do_not_leak_a_key_to_the_document(self):
        self.device.sudo().write({"api_key": "LIVE-KEY-987654"})
        invoice = self.ke_invoice()
        with self.demo_fails(EtimsError("refused", code="910")):
            self.run_queue()
        document = invoice.ke_etims_document_id
        self.assertNotIn("LIVE-KEY-987654", document.last_error or "")
        self.assertNotIn("LIVE-KEY-987654", "".join(document.message_ids.mapped("body")))

    def test_another_companys_queue_is_invisible(self):
        invoice = self.ke_invoice()
        manager = self.env["res.users"].create({
            "name": "US manager", "login": "us_manager",
            "company_id": self.us_company.id, "company_ids": [Command.set(self.us_company.ids)],
            "group_ids": [Command.set([self.env.ref("x_ke_etims.group_ke_etims_manager").id])],
        })
        documents = self.env["ke.etims.document"].with_user(manager).search([])
        self.assertNotIn(invoice.ke_etims_document_id, documents)
        self.assertFalse(self.env["ke.etims.device"].with_user(manager).search([]))
