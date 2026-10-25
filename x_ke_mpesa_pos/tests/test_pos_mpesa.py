# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The POS side: what the terminal asks the server, and what closing does."""

import json
from urllib.parse import urlsplit

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.addons.x_ke_mpesa.controllers.main import KIND_BY_PATH
from odoo.addons.x_ke_mpesa.tests.fake_daraja import SANDBOX_PASSKEY, FakeDaraja


@tagged("post_install", "-at_install")
class TestPosMpesa(TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        cls.fake = FakeDaraja().start()
        cls.addClassCleanup(cls.fake.stop)
        cls.env["ir.config_parameter"].sudo().set_param("web.base.url", "https://pay.example.co.ke")
        app = cls.env["mpesa.app"].create({
            "name": "Fake", "company_id": cls.company.id, "environment": "sandbox",
            "consumer_key": cls.fake.consumer[0], "consumer_secret": cls.fake.consumer[1],
            "api_url": cls.fake.url,
        })
        Shortcode = cls.env["mpesa.shortcode"]
        cls.store = Shortcode.create({"kind": "store", "number": "174379",
                                      "company_id": cls.company.id, "app_id": app.id,
                                      "passkey": SANDBOX_PASSKEY})
        cls.till = Shortcode.create({"kind": "till", "number": "600100", "parent_id": cls.store.id,
                                     "company_id": cls.company.id, "app_id": app.id})
        cls.other_till = Shortcode.create({"kind": "till", "number": "600101",
                                           "parent_id": cls.store.id,
                                           "company_id": cls.company.id, "app_id": app.id})
        (cls.store | cls.till | cls.other_till).action_setup_accounting()
        (cls.store | cls.till | cls.other_till).action_register_c2b()
        cls.config = cls.basic_config
        cls.till.pos_config_ids = cls.config
        action = cls.till.action_create_pos_payment_method()
        cls.mpesa_pm = cls.env["pos.payment.method"].browse(action["res_id"])
        cls.config.payment_method_ids |= cls.mpesa_pm
        cls.feed = cls.create_product("Layers mash 50kg", cls.categ_basic, 2500, tax_ids=[])
        cls.urls = dict(cls.fake.c2b_urls)

    def setUp(self):
        super().setUp()
        self.fake.reset()
        self.fake.c2b_urls.update(self.urls)
        self.open_new_session()

    # -- helpers ----------------------------------------------------------

    def send(self, url, body):
        _empty, _pay, _ke, token, subpath = urlsplit(url).path.split("/", 4)
        return self.env["mpesa.callback"].sudo()._receive(
            KIND_BY_PATH[subpath], token, json.dumps(body).encode(), "196.201.214.10")

    def deliver(self, **kwargs):
        return self.fake.deliver(self.send, **kwargs)

    def call(self, method, **data):
        data.setdefault("session_id", self.pos_session.id)
        return getattr(self.mpesa_pm, method)(data)

    def sell(self, amount_lines=1, payment=None):
        """A synced order paid by one M-Pesa line carrying what the POS set."""
        order = self.create_ui_order_data([(self.feed, amount_lines)],
                                          payments=[(self.mpesa_pm, 2500 * amount_lines)])
        if payment:
            order["payment_ids"][0][2].update(payment)
        result = self.env["pos.order"].sync_from_ui([order])
        return self.env["pos.order"].browse(result["pos.order"][0]["id"])

    def close(self):
        self.pos_session.action_pos_session_validate()
        self.assertEqual(self.pos_session.state, "closed")

    # -- configuration ----------------------------------------------------

    def test_method_is_wired_to_its_till(self):
        self.assertEqual(self.mpesa_pm.use_payment_terminal, "mpesa")
        self.assertEqual(self.mpesa_pm.journal_id, self.till.journal_id)
        self.assertEqual(self.mpesa_pm.outstanding_account_id,
                         self.till.journal_id.inbound_payment_method_line_ids.payment_account_id[:1])
        # A method no open session uses yet: Odoo freezes the others.
        spare = self.env["pos.payment.method"].browse(
            self.other_till.action_create_pos_payment_method()["res_id"])
        with self.assertRaises(ValidationError):
            spare.journal_id = self.till.journal_id

    def test_till_only_at_its_outlets(self):
        outlet_x = self.env["pos.config"].create({"name": "Outlet X"})
        outlet_y = self.env["pos.config"].create({"name": "Outlet Y"})
        self.other_till.pos_config_ids = outlet_x
        spare = self.env["pos.payment.method"].browse(
            self.other_till.action_create_pos_payment_method()["res_id"])
        self.assertEqual(spare.config_ids, outlet_x)
        with self.assertRaises(ValidationError):
            spare.config_ids |= outlet_y

    def test_rounding_warning(self):
        self.assertTrue(self.mpesa_pm.mpesa_rounding_warning)

    # -- STK at the till --------------------------------------------------

    def test_stk_sale_settles_at_close(self):
        self.fake.script_stk(0, receipt="QPOSSTK001")
        tx = self.call("mpesa_stk_request", amount=2500, phone="0708374149",
                       line_uuid="line-1", order_uuid="order-1", order_name="Shop/0001")
        self.assertEqual(tx["state"], "requested")
        self.assertEqual(self.fake.requests[-1][1]["PartyB"], "600100")
        self.deliver()
        status = self.call("mpesa_status", transaction_id=tx["id"])
        self.assertEqual(status["state"], "confirmed")
        self.assertEqual(status["receipt"], "QPOSSTK001")
        record = self.env["mpesa.transaction"].browse(tx["id"])
        self.assertEqual(record.allocation_state, "claimed", "not in the queue while the till holds it")
        order = self.sell(payment={"mpesa_tx_ref": tx["id"], "mpesa_receipt": "QPOSSTK001"})
        self.assertEqual(record.pos_payment_id.pos_order_id, order)
        self.assertEqual(record.pos_order_id, order)
        self.assertEqual(order.payment_ids.transaction_id, "QPOSSTK001")
        self.close()
        self.assertEqual(record.allocation_state, "allocated")
        self.assertTrue(record.statement_line_id.is_reconciled)
        self.assertEqual(self.pos_session.mpesa_expected, 2500)
        self.assertEqual(self.pos_session.mpesa_confirmed, 2500)
        self.assertEqual(self.pos_session.mpesa_difference, 0)
        row = self.env["mpesa.report.pos.session"].search([("session_id", "=", self.pos_session.id)])
        self.assertEqual(row.status, "green")

    def test_stk_failure_is_reported_to_the_till(self):
        self.fake.script_stk(1032)
        tx = self.call("mpesa_stk_request", amount=2500, phone="0708374149", line_uuid="line-2")
        self.deliver()
        status = self.call("mpesa_status", transaction_id=tx["id"])
        self.assertEqual(status["state"], "failed")
        self.assertIn("cancelled", status["message"])

    def test_decimals_are_refused(self):
        answer = self.call("mpesa_stk_request", amount=2500.5, phone="0708374149")
        self.assertIn("whole shillings", answer["error"])

    def test_other_point_of_sale_cannot_use_the_till(self):
        other = self.env["pos.config"].create({"name": "Outlet Y"})
        other_session = self.env["pos.session"].create({"config_id": other.id, "user_id": self.env.uid})
        with self.assertRaises(UserError):
            self.call("mpesa_stk_request", amount=10, phone="0708374149",
                      session_id=other_session.id)

    # -- paid to till -------------------------------------------------------

    def test_paid_to_till_claim(self):
        receipt = self.fake.c2b_payment("600100", 2500, first_name="WANJIKU")
        self.fake.c2b_payment("600100", 999)
        self.fake.c2b_payment("600101", 2500)  # the till next door
        self.deliver()
        offered = self.call("mpesa_unclaimed_receipts", amount=2500)
        self.assertEqual(len(offered), 1, "exact amount, this till only")
        self.assertEqual(offered[0]["name"], "WANJIKU")
        self.assertNotIn("0708", offered[0]["msisdn"])
        claimed = self.call("mpesa_claim", transaction_id=offered[0]["id"], amount=2500,
                            order_uuid="order-3", order_name="Shop/0003")
        self.assertEqual(claimed["receipt"], receipt)
        self.assertFalse(self.call("mpesa_unclaimed_receipts", amount=2500), "gone from the list")
        again = self.call("mpesa_claim", transaction_id=offered[0]["id"], amount=2500)
        self.assertIn("already been used", again["error"])
        self.sell(payment={"mpesa_tx_ref": claimed["id"], "mpesa_receipt": receipt})
        self.close()
        tx = self.env["mpesa.transaction"].browse(claimed["id"])
        self.assertEqual(tx.allocation_state, "allocated")

    def test_claim_of_another_amount_is_refused(self):
        self.fake.c2b_payment("600100", 999)
        self.deliver()
        tx = self.env["mpesa.transaction"].search([("amount", "=", 999)])
        answer = self.call("mpesa_claim", transaction_id=tx.id, amount=2500)
        self.assertIn("is for", answer["error"])

    def test_store_receipt_taken_by_the_till(self):
        """The confirmation only named the store: the cashier's pick decides."""
        receipt = self.fake.c2b_payment("174379", 2500)
        self.deliver()
        tx = self.env["mpesa.transaction"].search([("receipt", "=", receipt)])
        self.assertEqual(tx.shortcode_id, self.store)
        offered = self.call("mpesa_unclaimed_receipts", amount=2500)
        self.assertEqual([o["id"] for o in offered], [tx.id])
        self.call("mpesa_claim", transaction_id=tx.id, amount=2500)
        self.assertEqual(tx.shortcode_id, self.till)
        self.assertEqual(tx.statement_line_id.journal_id, self.till.journal_id)

    def test_released_claim_goes_back_to_the_queue(self):
        self.fake.c2b_payment("600100", 2500)
        self.deliver()
        offered = self.call("mpesa_unclaimed_receipts", amount=2500)
        self.call("mpesa_claim", transaction_id=offered[0]["id"], amount=2500, order_uuid="order-9")
        self.call("mpesa_release", transaction_id=offered[0]["id"], order_uuid="order-9")
        tx = self.env["mpesa.transaction"].browse(offered[0]["id"])
        self.assertEqual(tx.allocation_state, "unallocated")

    # -- typed receipts ---------------------------------------------------

    def test_typed_receipt_confirmed_after_closing(self):
        typed = self.call("mpesa_manual_receipt", receipt="QPOSMAN001", amount=2500,
                          order_uuid="order-4", order_name="Shop/0004")
        self.assertTrue(typed["manual"])
        self.sell(payment={"mpesa_tx_ref": typed["id"], "mpesa_receipt": "QPOSMAN001"})
        self.close()
        tx = self.env["mpesa.transaction"].browse(typed["id"])
        self.assertEqual(self.pos_session.mpesa_unconfirmed_count, 1)
        self.assertEqual(self.pos_session.mpesa_difference, 2500)
        # M-PESA confirms it the next morning: it settles against the session.
        self.fake.c2b_payment("600100", 2500, receipt="QPOSMAN001")
        self.deliver()
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.allocation_state, "allocated")

    def test_receipt_typed_offline_is_checked_at_sync(self):
        order = self.sell(payment={"mpesa_receipt": "QOFFLINE01", "mpesa_manual": True})
        tx = self.env["mpesa.transaction"].search([("receipt", "=", "QOFFLINE01")])
        self.assertEqual(tx.state, "manual")
        self.assertEqual(tx.pos_payment_id.pos_order_id, order)
        self.assertEqual(tx.exception, "unconfirmed_manual")

    def test_typed_receipt_already_confirmed(self):
        receipt = self.fake.c2b_payment("600100", 2500)
        self.deliver()
        answer = self.call("mpesa_manual_receipt", receipt=receipt, amount=2500, order_uuid="o5")
        self.assertFalse(answer.get("manual"))
        self.assertEqual(answer["state"], "confirmed")
        again = self.call("mpesa_manual_receipt", receipt=receipt, amount=2500, order_uuid="o6")
        self.assertIn("already been used", again["error"])

    def test_typed_receipt_of_the_till_next_door(self):
        receipt = self.fake.c2b_payment("600101", 2500)
        self.deliver()
        answer = self.call("mpesa_manual_receipt", receipt=receipt, amount=2500)
        self.assertIn("not this till", answer["error"])

    def test_one_receipt_two_orders(self):
        self.fake.c2b_payment("600100", 2500, receipt="QTWICE0001")
        self.deliver()
        tx = self.env["mpesa.transaction"].search([("receipt", "=", "QTWICE0001")])
        self.sell(payment={"mpesa_tx_ref": tx.id, "mpesa_receipt": "QTWICE0001"})
        self.sell(payment={"mpesa_tx_ref": tx.id, "mpesa_receipt": "QTWICE0001"})
        self.assertEqual(tx.exception, "double_claim")

    def test_abandoned_claim_is_released_at_close(self):
        self.fake.c2b_payment("600100", 2500)
        self.deliver()
        offered = self.call("mpesa_unclaimed_receipts", amount=2500)
        self.call("mpesa_claim", transaction_id=offered[0]["id"], amount=2500, order_uuid="lost")
        self.sell(payment={"mpesa_receipt": "QOTHER0001"})
        self.close()
        tx = self.env["mpesa.transaction"].browse(offered[0]["id"])
        self.assertFalse(tx.claimed)
        self.assertEqual(tx.allocation_state, "unallocated")
