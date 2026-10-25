# -*- coding: utf-8 -*-
"""POS orders through the same queue: signed from the till, pending offline."""

from random import randint

from odoo import Command, fields
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.x_ke_etims.backends import EtimsError
from odoo.addons.x_ke_etims.tests.common import KeEtimsCase


class KePosCase(KeEtimsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.sudo().group_ids = [Command.link(cls.env.ref("point_of_sale.group_pos_manager").id)]
        cls.product.write({"available_in_pos": True})
        cls.cash = cls.env["pos.payment.method"].create({
            "name": "Cash KES",
            "journal_id": cls.company_data["default_journal_cash"].id,
            "company_id": cls.ke_company.id,
        })
        cls.mpesa = cls.env["pos.payment.method"].create({
            "name": "M-Pesa",
            "journal_id": cls.company_data["default_journal_bank"].id,
            "company_id": cls.ke_company.id,
            "ke_etims_payment_type_id": cls.env["ke.etims.code"]._ke_etims_find("07", "06").id,
        })
        cls.config = cls.env["pos.config"].with_company(cls.ke_company).create({
            "name": "Nairobi Till 1",
            "company_id": cls.ke_company.id,
            "invoice_journal_id": cls.company_data["default_journal_sale"].id,
            "payment_method_ids": [Command.set((cls.cash | cls.mpesa).ids)],
        })



@tagged("post_install", "-at_install")
class TestPos(KePosCase):

    def setUp(self):
        super().setUp()
        self.config.open_ui()
        self.session = self.config.current_session_id
        self.session.set_opening_control(0, None)

    def order(self, product=None, qty=1.0, refund_of=None, invoiced=False, customer=None,
              method=None):
        product = product or self.product
        taxes = product.taxes_id.filtered(lambda t: t.company_id == self.ke_company)
        price = product.lst_price
        amounts = taxes.compute_all(price, self.ke_company.currency_id, qty)
        line = {
            "price_unit": price, "product_id": product.id, "qty": qty, "discount": 0.0,
            "price_subtotal": amounts["total_excluded"],
            "price_subtotal_incl": amounts["total_included"],
            "tax_ids": [Command.set(taxes.ids)], "pack_lot_ids": [],
        }
        if refund_of:
            line["refunded_orderline_id"] = refund_of.lines[0].id
        total = amounts["total_included"]
        uid = "%05d-%03d-%04d" % (randint(1, 99999), randint(1, 999), randint(1, 9999))
        data = {
            "amount_paid": total, "amount_return": 0, "amount_total": total,
            "amount_tax": total - amounts["total_excluded"],
            "date_order": fields.Datetime.to_string(fields.Datetime.now()),
            "fiscal_position_id": False, "pricelist_id": False,
            "name": "Order %s" % uid, "last_order_preparation_change": "{}",
            "lines": [Command.create(line)],
            "partner_id": customer and customer.id,
            "session_id": self.session.id,
            "payment_ids": [Command.create({"amount": total, "name": fields.Datetime.now(),
                                            "payment_method_id": (method or self.cash).id})],
            "uuid": uid, "user_id": self.env.uid, "to_invoice": invoiced,
        }
        result = self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].browse(result["pos.order"][-1]["id"])

    def test_a_paid_order_is_queued_and_the_till_gets_its_signature(self):
        order = self.order()
        document = order.ke_etims_document_id
        self.assertEqual(document.state, "to_send", "Syncing queues; it does not sign.")
        values = order.ke_etims_sign_from_ui()[0]
        self.assertEqual(values["ke_etims_state"], "sent")
        self.assertTrue(values["ke_etims_signature"])
        self.assertEqual(values["ke_etims_kra_invoice_no"], 1)
        self.assertEqual(values["ke_etims_receipt_label"], "NS")
        self.assertTrue(values["ke_etims_qr_url"])
        self.assertRegex(values["ke_etims_signed_at_display"], r"^\d\d/\d\d/\d{4} \d\d:\d\d:\d\d$")
        self.assertTrue(values["ke_etims_is_demo"])

    def test_offline_the_till_prints_pending_and_the_queue_signs_later(self):
        order = self.order()
        with self.demo_fails(EtimsError("Could not connect", retryable=True)):
            values = order.ke_etims_sign_from_ui()[0]
        self.assertEqual(values["ke_etims_state"], "to_send")
        self.assertFalse(values["ke_etims_signature"])
        self.run_queue()
        self.assertEqual(order.ke_etims_sign_from_ui()[0]["ke_etims_state"], "sent")

    def test_a_refund_is_a_credit_note_of_its_order(self):
        order = self.order()
        order.ke_etims_sign_from_ui()
        refund = self.order(qty=-1.0, refund_of=order)
        document = refund.ke_etims_document_id
        self.assertEqual(document.kind, "credit_note")
        self.assertEqual(document.reason_code, "06")
        values = refund.ke_etims_sign_from_ui()[0]
        self.assertEqual(values["ke_etims_state"], "sent", document.last_error)
        self.assertEqual(values["ke_etims_receipt_label"], "NC")
        self.assertEqual(document.original_document_id, order.ke_etims_document_id)
        self.assertIn(order.ke_etims_document_id.scu_id, values["ke_etims_original_cu_invoice"])
        sale = refund._ke_etims_prepare(document)
        self.assertEqual(sale.receipt_type, "R")
        self.assertGreater(sale.lines[0].quantity, 0, "KRA takes credit note quantities positive.")

    def test_an_invoiced_order_is_signed_once_as_its_invoice(self):
        order = self.order(invoiced=True, customer=self.customer)
        self.assertTrue(order.account_move)
        own = self.env["ke.etims.document"].search(
            [("res_model", "=", "pos.order"), ("res_id", "=", order.id)])
        self.assertFalse(own, "The order itself is not declared; its invoice is.")
        self.assertEqual(order.ke_etims_document_id, order.account_move.ke_etims_document_id)
        values = order.ke_etims_sign_from_ui()[0]
        self.assertEqual(values["ke_etims_state"], "sent")
        self.assertEqual(order.account_move.ke_etims_state, "sent")

    def test_the_payment_method_decides_the_kra_payment_type(self):
        self.assertEqual(self.cash.ke_etims_payment_type_id.code, "01")
        order = self.order(method=self.mpesa)
        self.assertEqual(order._ke_etims_payment_type(), "06")

    def test_the_till_knows_whether_to_ask(self):
        self.assertTrue(self.config.ke_etims_enabled)
        self.device.active = False
        self.config.invalidate_recordset()
        self.assertFalse(self.config.ke_etims_enabled)
        order = self.order()
        self.assertFalse(order.ke_etims_document_id)
        self.assertEqual(order.ke_etims_sign_from_ui()[0]["ke_etims_state"], False)

    def test_the_fields_reach_the_till(self):
        order = self.order()
        loaded = order.read_pos_data([], self.config)["pos.order"][0]
        for name in ("ke_etims_applicable", "ke_etims_state", "ke_etims_signature",
                     "ke_etims_qr_url", "ke_etims_signed_at_display"):
            self.assertIn(name, loaded)


@tagged("post_install", "-at_install")
class TestPosTour(KePosCase, HttpCase):

    def test_the_till_prints_the_signature(self):
        new_test_user(
            self.env, login="ke_till", name="Till Operator", company_id=self.ke_company.id,
            company_ids=self.ke_company.ids,
            groups="base.group_user,point_of_sale.group_pos_user,account.group_account_invoice")
        self.start_tour("/pos/ui?config_id=%d" % self.config.id, "KeEtimsPosReceiptTour",
                        login="ke_till")
        order = self.env["pos.order"].search([("config_id", "=", self.config.id)], limit=1)
        self.assertEqual(order.ke_etims_state, "sent")
