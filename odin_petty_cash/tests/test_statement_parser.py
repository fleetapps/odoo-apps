from odoo.tests import TransactionCase, tagged

from ..models.mpesa_statement import group_rows, parse_pdf_text, parse_table, send_money_charge, split_party

PDF_TEXT = """M-PESA STATEMENT
Receipt No. Completion Time Details Transaction Status Paid In Withdrawn Balance
UK30R1J9LE 2026-11-03 11:20:05 Customer Transfer Charge Completed -7.00 61,603.00
UK30R1J9LE 2026-11-03 11:20:05 Customer Transfer to 0712***633 - JUSTUS Completed -240.00 61,610.00
KIANGA
UK30R1HQ0Z 2026-11-03 11:02:40 Funds received from 0722***100 - RINZ DIRECTOR Completed 50,000.00 61,850.00
UK30R1F8QM 2026-11-03 08:12:13 Pay Bill to 400200 - SHELL VIWANDANI Acc. KDU865J Completed -6,500.00 11,850.00
UK30R1F8QM 2026-11-03 08:12:13 Pay Bill Charge Completed -35.75 11,814.25
UK30R1ZZZZ 2026-11-03 07:00:00 Customer Transfer to 0700***000 - FAILED GUY Failed -100.00 18,350.00
Page 1 of 1
"""


@tagged("post_install", "-at_install")
class TestStatementParser(TransactionCase):

    def test_pdf_rows_grouped_with_charges(self):
        txs = group_rows(parse_pdf_text(PDF_TEXT))
        by_code = {t["code"]: t for t in txs}
        self.assertEqual(len(txs), 3, "the failed row is left out")
        milk = by_code["UK30R1J9LE"]
        self.assertEqual((milk["amount"], milk["charge"], milk["kind"]), (240.0, 7.0, "payment"))
        self.assertEqual(milk["party"], "JUSTUS KIANGA", "a wrapped Details cell is joined back")
        fuel = by_code["UK30R1F8QM"]
        self.assertEqual((fuel["amount"], fuel["charge"]), (6500.0, 35.75))
        self.assertEqual((fuel["party"], fuel["number"], fuel["account"]), ("SHELL VIWANDANI", "400200", "KDU865J"))
        topup = by_code["UK30R1HQ0Z"]
        self.assertEqual((topup["kind"], topup["amount"]), ("topup", 50000.0))
        self.assertEqual([t["code"] for t in txs], ["UK30R1F8QM", "UK30R1HQ0Z", "UK30R1J9LE"], "oldest first")

    def test_table(self):
        table = [
            ["Detailed statement"], [],
            ["Receipt No.", "Completion Time", "Details", "Transaction Status", "Paid In", "Withdrawn", "Balance"],
            ["UK30R1H2AS", "2026-11-03 10:30:00", "Customer Transfer to 0723***614 - PAUL LUNYASI", "Completed", "", "200.00", "1,000.00"],
            ["UK30R1H2AS", "2026-11-03 10:30:00", "Customer Transfer of Funds Charge", "Completed", "", "7.00", "993.00"],
        ]
        txs = group_rows(parse_table(table))
        self.assertEqual(len(txs), 1)
        self.assertEqual((txs[0]["amount"], txs[0]["charge"], txs[0]["party"]), (200.0, 7.0, "PAUL LUNYASI"))

    def test_tariff_and_party(self):
        self.assertEqual(send_money_charge(100), 0)
        self.assertEqual(send_money_charge(240), 7)
        self.assertEqual(send_money_charge(4800), 57)
        self.assertEqual(split_party("Merchant Payment to 123456 - NAIVAS IMAARA")[0], "NAIVAS IMAARA")
