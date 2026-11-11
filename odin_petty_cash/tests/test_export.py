import io
from datetime import date

import openpyxl
import xlsxwriter

from odoo import http
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..controllers.export import PettyCashExport


@tagged("post_install", "-at_install")
class TestPettyCashExport(AccountTestInvoicingCommon, HttpCase):
    """Export today or yesterday: the day's rows between the balance brought
    forward and the balance carried forward, as the month's page shows them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Account = cls.env["account.account"]
        float_account = Account.create({"name": "Petty Cash - M-Pesa Float", "code": "120098",
                                        "account_type": "asset_cash"})
        journal = cls.env["account.journal"].create({
            "name": "Petty Cash", "code": "PCX", "type": "cash", "default_account_id": float_account.id})
        charges = Account.create({"name": "Bank charges", "code": "513898", "account_type": "expense"})
        cls.flt = cls.env["odin.petty.cash.float"].create({
            "name": "M-Pesa float", "journal_id": journal.id, "charge_account_id": charges.id})
        cls.cashier = new_test_user(cls.env, "pc_export_cashier", groups="odin_petty_cash.group_petty_cash_user")
        Line = cls.env["odin.petty.cash.line"]

        def line(day, amount, **vals):
            return Line.create({"float_id": cls.flt.id, "date": day, "description": "fuel kdu",
                                "amount": amount, "method": "cash", **vals})

        line("2026-10-31", 10000, kind="topup", description="FLOAT TOP UP")
        line("2026-11-02", 1000)
        line("2026-11-03", 200)
        line("2026-11-03", 3000).action_cancel()
        cls.sent = line("2026-11-03", 600, method="mpesa", phone="0723413614")
        line("2026-11-04", 400)

    def test_a_day_between_its_balances(self):
        day = self.env["odin.petty.cash.float"].with_user(self.cashier).get_book_day(self.flt.id, "2026-11-03")
        self.assertEqual(day["day"], "2026-11-03")
        self.assertEqual([r["amount"] for r in day["rows"]], [200, 3000, 600], "the day's rows only")
        self.assertEqual(day["opening"], 9000, "brought forward: the top-up less the day before")
        self.assertTrue(self.sent.charge, "sending to a phone has a charge")
        self.assertEqual(day["closing"], 9000 - 200 - 600 - self.sent.charge,
                         "carried forward: the cancelled voucher does not move it")
        month = self.env["odin.petty.cash.float"].get_book(self.flt.id, 2026, 11)
        self.assertEqual(day["closing"], month["rows"][3]["balance"], "the same figure the month shows")

    def test_the_first_of_the_month_brings_forward_the_month_before(self):
        first = self.env["odin.petty.cash.float"].get_book_day(self.flt.id, "2026-11-01")
        self.assertEqual((first["opening"], first["rows"], first["closing"]), (10000, [], 10000))
        last = self.env["odin.petty.cash.float"].get_book_day(self.flt.id, "2026-10-31")
        self.assertEqual((last["opening"], last["closing"]), (0, 10000))

    def test_the_sheet_leaves_cancelled_vouchers_out_of_the_totals(self):
        day = self.env["odin.petty.cash.float"].get_book_day(self.flt.id, "2026-11-03")
        output = io.BytesIO()
        book = xlsxwriter.Workbook(output, {"in_memory": True})
        export = PettyCashExport()
        export._write_sheet(book.add_worksheet("3 NOV"), export._formats(book), "PETTY CASH BOOK",
                            "M-Pesa float", date(2026, 11, 3), day)
        book.close()
        sheet = openpyxl.load_workbook(io.BytesIO(output.getvalue())).active
        self.assertEqual((sheet["B5"].value, sheet["K5"].value), ("BALANCE B/F", 9000))
        self.assertEqual([sheet[f"H{r}"].value for r in (6, 7, 8)], [200, 3000, 600])
        self.assertEqual(sheet["B9"].value, "TOTALS / BALANCE C/F")
        self.assertEqual(sheet["H9"].value, 800, "paid: not the cancelled 3,000")
        self.assertEqual(sheet["J9"].value, 800 + self.sent.charge)
        self.assertEqual(sheet["K9"].value, day["closing"])

    def test_the_export_route_sends_the_day(self):
        self.authenticate(self.cashier.login, self.cashier.login)
        response = self.url_open("/odin_petty_cash/export", data={
            "float_id": self.flt.id, "scope": "day", "day": "2026-11-03",
            "csrf_token": http.Request.csrf_token(self)})
        self.assertEqual(response.status_code, 200)
        self.assertIn("2026-11-03.xlsx", response.headers["Content-Disposition"])
        workbook = openpyxl.load_workbook(io.BytesIO(response.content))
        self.assertEqual(workbook.sheetnames, ["3 NOV"])
        sheet = workbook.active
        self.assertEqual(sheet["A1"].value, "PETTY CASH BOOK · 3 NOVEMBER 2026")
        self.assertEqual(sheet["K9"].value, 9000 - 800 - self.sent.charge)
        # The month export still works the way it did.
        response = self.url_open("/odin_petty_cash/export", data={
            "float_id": self.flt.id, "scope": "month", "year": 2026, "month": 11,
            "csrf_token": http.Request.csrf_token(self)})
        self.assertEqual(openpyxl.load_workbook(io.BytesIO(response.content)).sheetnames, ["NOVEMBER"])
