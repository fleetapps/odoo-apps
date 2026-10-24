# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The report views answer the questions they are named after."""

from odoo.tests import tagged

from .common import MpesaCommon


@tagged("post_install", "-at_install")
class TestReports(MpesaCommon):

    def test_till_day_goes_red_then_green(self):
        invoice = self.invoice(300)
        self.fake.c2b_payment("600200", 300, bill_ref=invoice.name)
        stray = self.fake.c2b_payment("600100", 200)
        self.deliver()
        self.env.flush_all()
        Report = self.env["mpesa.report.till.day"]
        paybill_row = Report.search([("shortcode_id", "=", self.paybill.id)])
        till_row = Report.search([("shortcode_id", "=", self.till.id)])
        self.assertEqual(paybill_row.status, "green")
        self.assertEqual(paybill_row.allocated_amount, 300)
        self.assertEqual(till_row.status, "red")
        self.assertEqual(till_row.unallocated_amount, 200)
        self.env["mpesa.allocate"].create({
            "transaction_ids": [(6, 0, self.transactions(receipt=stray).ids)],
            "mode": "partner", "partner_id": self.partner_a.id}).action_confirm()
        self.env.flush_all()
        self.assertEqual(Report.search([("shortcode_id", "=", self.till.id)]).status, "green")

    def test_unallocated_ageing(self):
        receipt = self.fake.c2b_payment("600100", 200)
        self.deliver()
        self.env.flush_all()
        row = self.env["mpesa.report.unallocated"].search([("receipt", "=", receipt)])
        self.assertEqual(row.age_bucket, "0")
        self.assertEqual(row.amount, 200)
        groups = self.env["mpesa.report.unallocated"]._read_group(
            [], ["age_bucket"], ["amount:sum"])
        self.assertTrue(groups)

    def test_callback_health_flags_silent_trading_hours(self):
        production = self.app.copy({"name": "Production", "environment": "production",
                                    "api_url": self.fake.url})
        self.till.write({"app_id": production.id, "state": "live",
                         "trading_hour_from": 0.0, "trading_hour_to": 24.0})
        self.fake.c2b_payment("600100", 100)
        self.deliver()
        self.env.flush_all()
        rows = self.env["mpesa.report.callback.health"].search(
            [("shortcode_id", "=", self.till.id)])
        self.assertTrue(rows.filtered("gap"), "thirteen silent days of trading hours")
        latest = rows.sorted("hour")[-1]
        self.assertEqual(latest.callback_count, 1)
        self.assertFalse(latest.gap)

    def test_collections_group_by_channel(self):
        self.fake.c2b_payment("600100", 100)
        self.fake.c2b_payment("600200", 250, bill_ref="X")
        self.deliver()
        groups = dict(self.env["mpesa.transaction"]._read_group(
            [("state", "=", "confirmed")], ["channel"], ["amount:sum"]))
        self.assertEqual(groups["till"], 100)
        self.assertEqual(groups["paybill"], 250)

    def test_partner_report(self):
        self.partner_b.mpesa_account_ref = "TENANT7"
        self.fake.c2b_payment("600200", 900, bill_ref="TENANT7")
        self.deliver()
        action = self.partner_b.action_open_mpesa_payments()
        found = self.env["mpesa.transaction"].search(action["domain"])
        self.assertEqual(found.mapped("amount"), [900])
        self.assertEqual(self.partner_b.mpesa_transaction_count, 1)
