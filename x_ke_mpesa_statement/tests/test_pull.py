# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The Pull API: setting it up, the 48-hour backfill, the hourly pull."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import StatementCommon, pull_row


@tagged("post_install", "-at_install")
class TestPull(StatementCommon):

    def setUp(self):
        super().setUp()
        self.fake.pull_rows.clear()

    def pull_queries(self):
        return [body for path, body in self.fake.requests if path == "/pulltransactions/v1/query"]

    def test_register(self):
        self.till.pull_nominated_number = "0722 000 111"
        self.till.action_register_pull()
        (path, body), = [r for r in self.fake.requests if r[0] == "/pulltransactions/v1/register"]
        self.assertEqual(body["ShortCode"], "600100")
        self.assertEqual(body["RequestType"], "Pull")
        self.assertEqual(body["NominatedNumber"], "254722000111")
        self.assertTrue(body["CallBackURL"].endswith(f"/pay/ke/{self.till.callback_token}/pull"))
        self.assertTrue(self.till.pull_registered_at)

    def test_register_needs_the_nominated_number(self):
        with self.assertRaisesRegex(UserError, "nominated number"):
            self.till.action_register_pull()

    def test_register_on_the_store_number(self):
        self.till.write({"pull_number": "174379", "pull_nominated_number": "0722000111"})
        self.till.action_register_pull()
        body = [b for p, b in self.fake.requests if p == "/pulltransactions/v1/register"][0]
        self.assertEqual(body["ShortCode"], "174379")

    def test_already_registered(self):
        self.till.pull_nominated_number = "0722000111"
        self.fake.fail_next["/pulltransactions/v1/register"] = (500, {
            "requestId": "x", "errorCode": "500.003.1001",
            "errorMessage": "Shortcode already registered"})
        self.till.action_register_pull()
        self.assertTrue(self.till.pull_registered_at)

    def test_setup_and_backfill(self):
        known = self.fake.c2b_payment("600100", 700)
        self.deliver()
        self.fake.pull_rows += [
            pull_row(known, 700, self.hours_ago(3)),
            pull_row("TMISSED001", 1250, self.hours_ago(30), bill_ref=""),
            pull_row("TMISSED002", 40, self.hours_ago(1)),
        ]
        self.till.action_setup_pull()
        provider = self.till.pull_provider_id
        self.assertEqual(provider.service, "mpesa_pull")
        self.assertEqual(self.till.journal_id.bank_statements_source, "online")
        self.assertEqual(provider.statement_creation_mode, "daily")
        # The backfill ran (jobs run inline in tests) and reached 48 hours back.
        first = self.pull_queries()[0]
        start = fields.Datetime.to_datetime(first["StartDate"]) - timedelta(hours=3)
        self.assertAlmostEqual(start, fields.Datetime.now() - timedelta(hours=48),
                               delta=timedelta(minutes=15))
        missed = self.transactions(receipt="TMISSED001")
        self.assertEqual(missed.source, "pull")
        self.assertEqual(missed.state, "confirmed")
        self.assertEqual(missed.shortcode_id, self.till)
        self.assertEqual(missed.msisdn_masked, "2547*****149", "never a full number")
        line = missed.statement_line_id
        self.assertEqual(line.unique_import_id, "MPESA-TMISSED001")
        self.assertEqual(line.journal_id, self.till.journal_id)
        self.assertEqual(line.amount, 1250)
        self.assertEqual(line.statement_id, self.till.journal_id._mpesa_daily_statement(
            missed.trans_date), "in the daily statement of the day it was paid")
        self.assertTrue(self.transactions(receipt="TMISSED002"))
        # The receipt C2B brought is still there once, with one line.
        tx = self.transactions(receipt=known)
        self.assertEqual(tx.source, "c2b")
        self.assertEqual(self.env["account.bank.statement.line"].search_count(
            [("unique_import_id", "=", f"MPESA-{known}")]), 1)
        self.assertTrue(self.till.message_ids.filtered(
            lambda m: "did not have yet" in (m.body or "")))
        # Receipts booked into earlier days moved the later days' opening balances.
        statements = self.env["account.bank.statement"].search(
            [("journal_id", "=", self.till.journal_id.id)])
        self.assertGreater(len(statements), 1)
        self.assertTrue(all(statements.mapped("is_valid")))
        self.assertTrue(all(statements.mapped("is_complete")))

    def test_a_live_till_registers_before_pulling(self):
        self.app.environment = "production"
        self.till.state = "live"
        with self.assertRaisesRegex(UserError, "Register the Pull API"):
            self.till.action_setup_pull()

    def test_setup_twice_keeps_one_provider(self):
        self.till.action_setup_pull()
        self.till.action_setup_pull()
        self.assertEqual(self.env["online.bank.statement.provider"].search_count(
            [("journal_id", "=", self.till.journal_id.id)]), 1)

    def test_setup_needs_a_journal(self):
        bare = self.env["mpesa.shortcode"].create({
            "kind": "till", "number": "600199", "parent_id": self.store.id,
            "company_id": self.company.id, "app_id": self.app.id})
        with self.assertRaisesRegex(UserError, "accounting"):
            bare.action_setup_pull()

    def test_hourly_pull_asks_for_the_last_two_hours(self):
        self.till.action_setup_pull()
        provider = self.till.pull_provider_id
        now = fields.Datetime.now()
        provider.write({"last_successful_run": now - timedelta(hours=1),
                        "next_run": now - timedelta(minutes=1)})
        self.fake.requests.clear()
        self.fake.pull_rows.append(pull_row("TLATE00001", 300, self.hours_ago(0.5)))
        provider._scheduled_pull()
        queries = self.pull_queries()
        self.assertTrue(queries)
        start = min(fields.Datetime.to_datetime(q["StartDate"]) for q in queries) \
            - timedelta(hours=3)
        self.assertAlmostEqual(start, now - timedelta(hours=2), delta=timedelta(minutes=5))
        self.assertTrue(self.transactions(receipt="TLATE00001"))
        self.assertGreater(provider.next_run, now)

    def test_after_an_outage_the_pull_reaches_back_to_the_last_run(self):
        self.till.action_setup_pull()
        provider = self.till.pull_provider_id
        now = fields.Datetime.now()
        provider.write({"last_successful_run": now - timedelta(hours=6),
                        "next_run": now - timedelta(hours=5)})
        self.fake.requests.clear()
        provider._scheduled_pull()
        start = min(fields.Datetime.to_datetime(q["StartDate"]) for q in self.pull_queries()) \
            - timedelta(hours=3)
        self.assertAlmostEqual(start, now - timedelta(hours=7), delta=timedelta(minutes=5))

    def test_older_than_48_hours_points_to_the_statement(self):
        self.till.action_setup_pull()
        provider = self.till.pull_provider_id
        now = fields.Datetime.now()
        self.fake.requests.clear()
        provider._pull(now - timedelta(days=5), now)
        self.assertTrue(provider.message_ids.filtered(
            lambda m: "48 hours" in (m.body or "") and "Org Portal" in (m.body or "")))
        start = min(fields.Datetime.to_datetime(q["StartDate"]) for q in self.pull_queries()) \
            - timedelta(hours=3)
        self.assertGreater(start, now - timedelta(hours=48))

    def test_pages(self):
        self.fake.pull_page_size = 2
        self.fake.pull_rows += [pull_row(f"TPAGE0000{i}", 100 + i, self.hours_ago(2))
                                for i in range(5)]
        self.till.action_setup_pull()
        for i in range(5):
            self.assertTrue(self.transactions(receipt=f"TPAGE0000{i}"), i)
        offsets = [int(q["OffSetValue"]) for q in self.pull_queries()]
        self.assertIn(4, offsets)

    def test_an_offset_daraja_ignores_does_not_loop(self):
        self.fake.pull_page_size = 2
        self.fake.pull_rows += [pull_row(f"TSAME0000{i}", 10, self.hours_ago(2))
                                for i in range(2)]
        original = self.fake._pull_query
        # Every page is the first page.
        self.fake._pull_query = lambda body: original(dict(body, OffSetValue="0"))
        try:
            rows = self.till._pull_rows(fields.Datetime.now() - timedelta(hours=3),
                                        fields.Datetime.now())
        finally:
            self.fake._pull_query = original
        self.assertEqual(len(rows), 2)

    def test_time_zone_setting(self):
        moment = self.hours_ago(5).replace(microsecond=0)
        self.fake.pull_rows += [pull_row("TZONE00001", 10, moment)]
        self.till.action_setup_pull()
        utc_time = self.transactions(receipt="TZONE00001").trans_time
        self.assertEqual(utc_time, moment.replace(tzinfo=None))

        self.company.mpesa_pull_time_zone = "nairobi"
        self.fake.pull_rows[:] = [pull_row("TZONE00002", 10, moment)]
        self.till.pull_provider_id._mpesa_backfill()
        # The same "Z" time read as Nairobi wall time is three hours earlier in UTC.
        self.assertEqual(self.transactions(receipt="TZONE00002").trans_time,
                         moment.replace(tzinfo=None) - timedelta(hours=3))

    def test_rows_that_are_not_receipts(self):
        self.fake.pull_rows += [
            pull_row("TB2C000001", 500, self.hours_ago(2), kind="b2c-payment"),
            pull_row("TNEG000001", -20, self.hours_ago(2)),
        ]
        self.till.action_setup_pull()
        self.assertFalse(self.transactions(receipt="TB2C000001"))
        self.assertFalse(self.transactions(receipt="TNEG000001"))

    def test_paybill_account_number(self):
        invoice = self.invoice(900)
        self.fake.pull_rows.append(pull_row("TPAYBL0001", 900, self.hours_ago(2),
                                            kind="c2b-pay-bill-debit", bill_ref=invoice.name,
                                            shortcode="600200"))
        self.paybill.action_setup_pull()
        tx = self.transactions(receipt="TPAYBL0001")
        self.assertEqual(tx.channel, "paybill")
        self.assertEqual(invoice.payment_state, "paid", "matched like any paybill receipt")

    def test_debug_shows_rows_and_books_nothing(self):
        self.till.action_setup_pull()
        provider = self.till.pull_provider_id
        self.fake.pull_rows.append(pull_row("TDEBUG0001", 10, self.hours_ago(1)))
        now = fields.Datetime.now()
        data = provider.with_context(account_statement_online_import_debug=True)._pull(
            now - timedelta(hours=2), now)
        rows = [item for chunk in data if isinstance(chunk, list) for item in chunk]
        self.assertEqual(rows[0]["transactionId"], "TDEBUG0001")
        self.assertEqual(rows[0]["msisdn"], "2547*****149")
        self.assertFalse(self.transactions(receipt="TDEBUG0001"))

    def test_pull_callback(self):
        body = {"Response": [[pull_row("TPOSTED001", 75, self.hours_ago(1))]]}
        self.send(self.till._callback_url("pull"), body)
        tx = self.transactions(receipt="TPOSTED001")
        self.assertEqual(tx.source, "pull")
        self.assertEqual(tx.shortcode_id, self.till)

    def test_a_bad_row_does_not_stop_the_others(self):
        self.fake.pull_rows += [pull_row("TGOOD00001", 10, self.hours_ago(1)),
                                pull_row("TGOOD00002", 20, self.hours_ago(1))]
        Tx = type(self.env["mpesa.transaction"])
        original = Tx._from_pull

        def flaky(model, shortcode, row):
            if row["transactionId"] == "TGOOD00001":
                raise ValueError("boom")
            return original(model, shortcode, row)

        Tx._from_pull = flaky
        try:
            with self.assertLogs("odoo.addons.x_ke_mpesa_statement", level="ERROR"):
                self.till.action_setup_pull()
        finally:
            Tx._from_pull = original
        self.assertTrue(self.transactions(receipt="TGOOD00002"))
        self.assertFalse(self.transactions(receipt="TGOOD00001"))
