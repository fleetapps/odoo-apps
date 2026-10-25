# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The M-PESA Org Portal statement: receipts, charges, sweeps, reversals and
M-PESA's own balance, into the till's daily statements."""

from datetime import datetime

from odoo.tests import tagged

from .common import StatementCommon

HEADER = """Account Holder:,CHOICE FEEDS LIMITED
Short Code:,600100
Account:,Merchant Account
Time Period:,From 20-10-2026 To 21-10-2026

Receipt No.,Completion Time,Initiation Time,Details,Transaction Status,Paid In,Withdrawn,Balance,Balance Confirmed,Reason Type,Other Party Info,Linked Transaction ID,A/C No.
"""


def row(receipt, when, details, paid_in="", withdrawn="", balance="", reason="",
        other="", linked="", account="", status="Completed"):
    cells = [receipt, when, when, details, status, paid_in, withdrawn, balance, "true",
             reason, other, linked, account]
    return ",".join(f'"{cell}"' if "," in cell else cell for cell in cells) + "\n"


@tagged("post_install", "-at_install")
class TestOrgStatement(StatementCommon):

    def setUp(self):
        super().setUp()
        # A receipt the C2B confirmation already brought, paid on 20 October.
        self.known = self.fake.c2b_payment("600100", 700, first_name="JANE",
                                           trans_time="20261020081500")
        self.deliver()
        self.journal = self.till.journal_id

    def statement(self):
        return HEADER + "".join([
            row(self.known, "2026-10-20 08:15:00",
                "Merchant Payment from 2547******149 - JANE WANJIKU", paid_in="700.00",
                balance="700.00", reason="Buy Goods Online",
                other="2547******149 - JANE WANJIKU"),
            row("TNEW000001", "2026-10-20 09:00:00",
                "Merchant Payment from 254712345678 - PETER OTIENO", paid_in="1,250.00",
                balance="1,950.00", reason="Buy Goods Online",
                other="254712345678 - PETER OTIENO"),
            row("TNEW000001", "2026-10-20 09:00:00", "Pay Merchant Charge",
                withdrawn="-6.25", balance="1,943.75", reason="Pay Merchant Charge"),
            row("TDECL00001", "2026-10-20 10:00:00", "Merchant Payment",
                paid_in="500.00", balance="1,943.75", reason="Buy Goods Online",
                status="Declined"),
            row("TSWP000001", "2026-10-20 18:00:00", "Business Settlement to ABC Bank",
                withdrawn="-1,900.00", balance="43.75",
                reason="Organization Settle Funds To Bank", other="ABC BANK"),
            row("TNEW000002", "2026-10-21 08:00:00",
                "Merchant Payment from 254799000111 - MARY AKINYI", paid_in="300.00",
                balance="343.75", reason="Buy Goods Online",
                other="254799000111 - MARY AKINYI"),
            row("TREV000001", "2026-10-21 09:00:00", "Reversal of TNEW000002",
                withdrawn="-100.00", balance="243.75", reason="Reversal", linked="TNEW000002"),
            '"Total",,,,,"2,750.00","-2,006.25",,,,,,\n',
        ])

    def line(self, uid):
        return self.env["account.bank.statement.line"].search([("unique_import_id", "=", uid)])

    def daily(self, day):
        return self.journal._mpesa_daily_statement(datetime.strptime(day, "%Y-%m-%d").date())

    def test_mapping_is_the_default_on_mpesa_journals(self):
        self.assertEqual(self.journal.default_sheet_mapping_id,
                         self.env.ref("x_ke_mpesa_statement.mapping_mpesa_org_portal"))

    def test_import(self):
        result = self.import_statement(self.journal, self.statement())
        self.assertIn("2 new receipts", result["notifications"][0])
        self.assertIn("1 rows already in Odoo", result["notifications"][0])
        self.assertIn("1 charges booked", result["notifications"][0])

        # The receipt C2B brought: nothing new, still one line.
        self.assertEqual(len(self.line(f"MPESA-{self.known}")), 1)
        self.assertEqual(self.transactions(receipt=self.known).exception, "none")

        # A receipt C2B missed: in the log, from the statement, on its day.
        new = self.transactions(receipt="TNEW000001")
        self.assertEqual(new.source, "statement")
        self.assertEqual(new.amount, 1250)
        self.assertEqual(new.customer_name, "PETER OTIENO")
        self.assertEqual(new.msisdn_masked, "2547*****678", "never a full number")
        self.assertEqual(new.trans_time, datetime(2026, 10, 20, 6, 0), "09:00 Nairobi")
        self.assertEqual(new.statement_line_id.statement_id, self.daily("2026-10-20"))
        self.assertNotIn("254712345678", new.statement_line_id.payment_ref)

        # Its charge: booked to the charges account.
        charge = self.line("MPESA-TNEW000001-CHG")
        self.assertEqual(charge.amount, -6.25)
        self.assertEqual(charge.mpesa_line_kind, "charge")
        self.assertTrue(charge.is_reconciled)
        charges_account = self.company.mpesa_charges_account_id
        self.assertEqual(charges_account.account_type, "expense")
        self.assertEqual(sum(charge.move_id.line_ids.filtered(
            lambda l: l.account_id == charges_account).mapped("debit")), 6.25)

        # The sweep: to the internal transfer account, for the bank side to meet.
        sweep = self.line("MPESA-TSWP000001-SWP")
        self.assertTrue(sweep.is_reconciled)
        self.assertEqual(sum(sweep.move_id.line_ids.filtered(
            lambda l: l.account_id == self.company.transfer_account_id).mapped("debit")), 1900)

        # The reversal: left for the accountant, and the receipt it reverses flagged.
        reversal = self.line("MPESA-TREV000001-REV")
        self.assertFalse(reversal.is_reconciled)
        self.assertEqual(self.transactions(receipt="TNEW000002").exception, "reversed")

        # Declined rows and the total at the foot are not money.
        self.assertFalse(self.transactions(receipt="TDECL00001"))
        self.assertFalse(self.transactions(receipt="TOTAL"))

        # M-PESA's closing balance, day by day, agrees with Odoo's.
        day1, day2 = self.daily("2026-10-20"), self.daily("2026-10-21")
        self.assertEqual(day1.mpesa_balance_reported, 43.75)
        self.assertEqual(day1.mpesa_balance_reported_at, datetime(2026, 10, 20, 15, 0))
        self.assertEqual(day1.mpesa_balance_gap, 0)
        self.assertEqual(day2.mpesa_balance_reported, 243.75)
        self.assertEqual(day2.mpesa_balance_gap, 0)
        self.assertTrue(day1.is_complete)
        self.assertIn(day1.id, result["statement_ids"])

    def test_import_twice(self):
        self.import_statement(self.journal, self.statement())
        result = self.import_statement(self.journal, self.statement())
        self.assertIn("0 new receipts", result["notifications"][0])
        self.assertIn("6 rows already in Odoo", result["notifications"][0])
        self.assertEqual(len(self.line("MPESA-TNEW000001-CHG")), 1)
        self.assertEqual(len(self.transactions(receipt="TNEW000001")), 1)

    def test_a_gap_shows(self):
        # Odoo never heard of the 700 receipt's day: the balances disagree.
        content = HEADER + row("TONLY00001", "2026-10-22 10:00:00", "Merchant Payment",
                               paid_in="100.00", balance="5,100.00",
                               reason="Buy Goods Online")
        self.import_statement(self.journal, content)
        statement = self.daily("2026-10-22")
        self.assertEqual(statement.mpesa_balance_reported, 5100)
        self.assertEqual(statement.mpesa_balance_odoo, 800, "700 from C2B and 100 here")
        self.assertEqual(statement.mpesa_balance_gap, 4300)

    def test_store_statement_with_its_tills_receipts(self):
        content = HEADER + "".join([
            row(self.known, "2026-10-20 08:15:00", "Merchant Payment", paid_in="700.00",
                balance="700.00", reason="Buy Goods Online"),
            row("TSTORE0001", "2026-10-20 09:30:00", "Merchant Payment", paid_in="60.00",
                balance="760.00", reason="Buy Goods Online"),
        ])
        self.import_statement(self.store.journal_id, content)
        self.assertEqual(self.transactions(receipt=self.known).exception, "none",
                         "the store's statement may list its tills' receipts")
        self.assertEqual(self.transactions(receipt="TSTORE0001").shortcode_id, self.store)
        statement = self.store.journal_id._mpesa_daily_statement(
            datetime(2026, 10, 20).date())
        self.assertEqual(statement.mpesa_balance_gap, 0, "the store's balance counts its tills")

    def test_an_export_without_the_optional_columns(self):
        content = HEADER.replace(",Linked Transaction ID,A/C No.", "") + \
            "TSHORT0001,2026-10-20 12:00:00,2026-10-20 12:00:00,Merchant Payment,Completed," \
            "80.00,,780.00,true,Buy Goods Online,JOHN\n"
        self.import_statement(self.journal, content)
        self.assertEqual(self.transactions(receipt="TSHORT0001").amount, 80)

    def test_paybill_account_number_in_the_statement(self):
        invoice = self.invoice(450)
        content = HEADER + row("TPAYB00001", "2026-10-20 11:00:00",
                               f"Pay Bill from 254722000333 - ALI HASSAN Acc. {invoice.name}",
                               paid_in="450.00", balance="450.00", reason="Pay Bill Online",
                               other="254722000333 - ALI HASSAN", account=invoice.name)
        self.import_statement(self.paybill.journal_id, content)
        tx = self.transactions(receipt="TPAYB00001")
        self.assertEqual(tx.bill_ref, invoice.name)
        self.assertEqual(invoice.payment_state, "paid")

    def test_the_pull_schedule_survives_an_import(self):
        self.till.action_setup_pull()
        provider = self.till.pull_provider_id
        self.import_statement(self.journal, self.statement())
        self.assertEqual(self.journal.online_bank_statement_provider_id, provider)
        self.assertEqual(self.journal.bank_statements_source, "online")

    def test_settlement_report(self):
        self.import_statement(self.journal, self.statement())
        self.env.flush_all()
        report = self.env["mpesa.report.settlement"].search(
            [("journal_id", "=", self.journal.id)])
        self.assertEqual(len(report), 1)
        self.assertEqual(str(report.month), "2026-10-01")
        self.assertEqual(report.shortcode_id, self.till)
        self.assertEqual(report.receipt_count, 3)
        self.assertEqual(report.received, 2250)
        self.assertEqual(report.charges, 6.25)
        self.assertEqual(report.swept, 1900)
        self.assertEqual(report.reversed_amount, 100)
        self.assertEqual(report.net, 243.75)
        self.assertEqual(report.unbooked_count, 1, "the reversal")

    def test_other_journals_import_as_before(self):
        mapping = self.env["account.statement.import.sheet.mapping"].create({
            "name": "Plain bank", "delimiter": "comma", "quotechar": '"',
            "float_thousands_sep": "comma", "float_decimal_sep": "dot",
            "header_lines_skip_count": 1, "timestamp_format": "%Y-%m-%d",
            "timestamp_column": "Date", "amount_type": "simple_value",
            "amount_column": "Amount", "description_column": "Label",
        })
        bank = self.company_data["default_journal_bank"]
        result = self.import_statement(bank, "Date,Label,Amount\n2026-10-20,Rent,-500.00\n",
                                       mapping=mapping)
        statement = self.env["account.bank.statement"].browse(result["statement_ids"])
        self.assertEqual(statement.journal_id, bank)
        self.assertEqual(statement.line_ids.amount, -500)
