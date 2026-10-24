# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""One receipt, one line, whichever of C2B, STK, Pull or a file saw it first."""

from datetime import datetime

from psycopg2 import IntegrityError

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import MpesaCommon


@tagged("post_install", "-at_install")
class TestDeduplication(MpesaCommon):

    def import_line(self, receipt, amount, journal=None):
        """What a statement import does: create the line with its import id."""
        journal = journal or self.till.journal_id
        vals = {"unique_import_id": f"MPESA-{receipt}", "amount": amount,
                "payment_ref": receipt}
        journal._statement_line_import_update_unique_import_id(vals, "600100")
        self.assertEqual(vals["unique_import_id"], f"MPESA-{receipt}",
                         "M-Pesa journals keep the canonical id")
        existing = self.env["account.bank.statement.line"].search(
            [("unique_import_id", "=", vals["unique_import_id"])])
        if existing:
            return existing
        return self.env["account.bank.statement.line"].create(
            dict(vals, journal_id=journal.id, date=datetime.now().date()))

    def test_c2b_then_file(self):
        receipt = self.fake.c2b_payment("600100", 450)
        self.deliver()
        line = self.import_line(receipt, 450)
        self.assertEqual(line, self.transactions(receipt=receipt).statement_line_id)

    def test_file_then_c2b(self):
        receipt = "QFILE00001"
        line = self.import_line(receipt, 450)
        tx = line.mpesa_transaction_id
        self.assertTrue(tx, "the imported line created its receipt")
        self.assertEqual(tx.source, "statement")
        self.fake.c2b_payment("600100", 450, receipt=receipt)
        self.deliver()
        self.assertEqual(self.transactions(receipt=receipt), tx)
        self.assertEqual(tx.statement_line_id, line)
        self.assertEqual(tx.msisdn_masked, "2547*****149", "the C2B details enrich it")
        self.assertEqual(self.env["account.bank.statement.line"].search_count(
            [("unique_import_id", "=", f"MPESA-{receipt}")]), 1)

    def test_pull_after_c2b(self):
        receipt = self.fake.c2b_payment("600100", 450)
        self.deliver()
        tx = self.env["mpesa.transaction"]._upsert_receipt(
            self.till, receipt, {"amount": 450}, "pull")
        self.assertEqual(tx, self.transactions(receipt=receipt))
        self.assertEqual(tx.source, "c2b", "the first channel keeps the record")

    def test_stk_then_c2b(self):
        self.fake.script_stk(0, receipt="QSTKC2B001")
        tx = self.stk(330)
        self.deliver()
        self.fake.c2b_payment("600100", 330, receipt="QSTKC2B001", first_name="BOB")
        self.deliver()
        self.assertEqual(self.transactions(receipt="QSTKC2B001"), tx)
        self.assertEqual(tx.customer_name, "BOB")
        self.assertEqual(self.env["account.bank.statement.line"].search_count(
            [("unique_import_id", "=", "MPESA-QSTKC2B001")]), 1)

    def test_c2b_then_stk(self):
        self.fake.script_stk(0, receipt="QC2BSTK001")
        tx = self.stk(330)
        stk_callback = self.fake.take()
        self.fake.c2b_payment("600100", 330, receipt="QC2BSTK001")
        self.deliver()
        c2b = self.transactions(receipt="QC2BSTK001")
        self.assertEqual(c2b.source, "c2b")
        for item in stk_callback:
            self.send(item["url"], item["body"])
        self.assertEqual(self.transactions(receipt="QC2BSTK001"), tx,
                         "the STK request absorbs the C2B record")
        self.assertFalse(c2b.exists())
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(self.env["account.bank.statement.line"].search_count(
            [("unique_import_id", "=", "MPESA-QC2BSTK001")]), 1)

    @mute_logger("odoo.sql_db")
    def test_receipt_is_unique_per_company(self):
        receipt = self.fake.c2b_payment("600100", 100)
        self.deliver()
        tx = self.transactions(receipt=receipt)
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            tx.copy({"receipt": receipt})
