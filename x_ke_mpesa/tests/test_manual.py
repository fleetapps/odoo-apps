# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Manual receipts: allowed when Daraja is down, believed only when M-PESA agrees."""

from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .common import MpesaCommon


@tagged("post_install", "-at_install")
class TestManualReceipts(MpesaCommon):

    def manual(self, receipt, amount, shortcode=None):
        return self.env["mpesa.transaction"]._manual_entry(shortcode or self.till, receipt, amount)

    def test_flagged_until_confirmed(self):
        tx = self.manual("qman000001", 500)
        self.assertEqual(tx.receipt, "QMAN000001")
        self.assertEqual(tx.state, "manual")
        self.assertEqual(tx.exception, "unconfirmed_manual")
        self.assertFalse(tx.statement_line_id, "no money in the bank on a cashier's word")
        self.fake.c2b_payment("600100", 500, receipt="QMAN000001")
        self.deliver()
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.exception, "none")
        self.assertTrue(tx.statement_line_id)

    def test_wrong_amount(self):
        tx = self.manual("QMAN000002", 5000)
        self.fake.c2b_payment("600100", 500, receipt="QMAN000002")
        self.deliver()
        self.assertEqual(tx.exception, "amount_mismatch")
        self.assertEqual(tx.amount, 500, "the books carry what M-PESA says")

    def test_other_till(self):
        tx = self.manual("QMAN000003", 500)
        self.fake.c2b_payment("600101", 500, receipt="QMAN000003")
        self.deliver()
        self.assertEqual(tx.exception, "cross_till")
        self.assertEqual(tx.shortcode_id, self.till_b)

    def test_receipt_used_twice(self):
        self.manual("QMAN000004", 500)
        with self.assertRaises(UserError):
            self.manual("QMAN000004", 500)

    def test_already_confirmed_receipt(self):
        receipt = self.fake.c2b_payment("600100", 500)
        self.deliver()
        self.assertEqual(self.manual(receipt, 500), self.transactions(receipt=receipt))
        with self.assertRaisesRegex(UserError, "is for"):
            self.manual(receipt, 400)
        with self.assertRaisesRegex(UserError, "not this till"):
            self.manual(receipt, 500, shortcode=self.till_b)

    def test_not_a_receipt(self):
        with self.assertRaises(UserError):
            self.manual("12", 500)

    def test_supervisor_alert(self):
        supervisor = new_test_user(self.env, login="supervisor",
                                   groups="base.group_user,x_ke_mpesa.group_mpesa_officer")
        self.till.supervisor_id = supervisor
        tx = self.manual("QMAN000005", 500)
        Tx = self.env["mpesa.transaction"]
        Tx._cron_alert_unconfirmed_manual()
        self.assertFalse(tx.alerted_at, "not yet")
        with freeze_time(fields.Datetime.now() + timedelta(minutes=31)):
            Tx._cron_alert_unconfirmed_manual()
        self.assertTrue(tx.alerted_at)
        activity = tx.activity_ids
        self.assertEqual(activity.user_id, supervisor)
        self.assertEqual(activity.activity_type_id,
                         self.env.ref("x_ke_mpesa.mail_activity_unconfirmed_receipt"))
        with freeze_time(fields.Datetime.now() + timedelta(minutes=60)):
            Tx._cron_alert_unconfirmed_manual()
        self.assertEqual(len(tx.activity_ids), 1, "one alert, not one every five minutes")

    def test_retention(self):
        self.fake.script_stk(0)
        tx = self.stk(100)
        self.deliver()
        self.assertEqual(tx.phone, "254708374149")
        self.company.mpesa_phone_retention_days = 30
        with freeze_time(fields.Datetime.now() + timedelta(days=31)):
            self.env["mpesa.transaction"]._cron_retention()
        self.assertFalse(tx.phone)
        self.assertEqual(tx.msisdn_masked, "2547*****149")
        payloads = tx.callback_ids.mapped("payload")
        self.assertTrue(payloads)
        self.assertFalse(any("254708374149" in payload for payload in payloads))
