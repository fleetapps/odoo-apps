import base64
from datetime import datetime

from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from ..models.mpesa_statement import group_rows, parse_pdf_text
from .test_petty_cash import TestPettyCash
from .test_statement_parser import PDF_TEXT

HEAD = "Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"


@tagged("post_install", "-at_install")
class TestPettyCashReview(TestPettyCash):
    """What the review found: each test is a way the book went wrong."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # As on site: neither is an accountant or a storekeeper.
        cls.cashier = new_test_user(cls.env, "pc_cashier", groups="odin_petty_cash.group_petty_cash_user")
        cls.approver = new_test_user(cls.env, "pc_approver", groups="odin_petty_cash.group_petty_cash_approver")

    def upload(self, csv_text, user=None):
        wizard = self.env["odin.petty.cash.import"].with_user(user or self.env.user).create({
            "float_id": self.flt.id, "filename": "statement.csv", "statement_file": base64.b64encode(csv_text.encode())})
        wizard.action_import()
        return wizard

    def voucher(self, **vals):
        return self.Line.create({"float_id": self.flt.id, "date": "2026-11-03", "description": "fuel kdu",
                                 "amount": 100, **vals})

    # ------------------------------------------------------------ numbers
    def test_voucher_numbers_are_never_given_twice(self):
        first, second = self.voucher(), self.voucher()
        number = second.voucher
        second.unlink()
        third = self.voucher()
        self.assertEqual((first.voucher, third.voucher), ("PCV/2026/11/001", "PCV/2026/11/003"),
                         "a deleted draft's number is not given again")
        third.date = "2026-12-01"
        self.assertEqual(third.voucher, "PCV/2026/12/001", "a new month starts at 001")
        self.assertNotEqual(self.voucher().voucher, number)

    # ------------------------------------------------------------ rights
    def test_cashier_opens_the_book_and_types(self):
        Float = self.env["odin.petty.cash.float"].with_user(self.cashier)
        self.voucher().action_approve()
        book = Float.get_book(self.flt.id, 2026, 11)
        self.assertEqual(book["float"]["book_balance"], -100.0, "the posted balance, read for the cashier")
        row = self.Line.with_user(self.cashier).book_save(False, {
            "float_id": self.flt.id, "date": "2026-11-04", "description": "fuel kdu", "amount": 50})
        self.assertEqual(row["status_key"], "ready")
        with self.assertRaises(UserError):
            self.Line.with_user(self.cashier).book_approve([row["id"]])
        self.assertEqual(self.Line.browse(row["id"]).state, "draft")

    def test_approver_who_is_not_an_accountant_approves(self):
        line = self.voucher(phone="0723413614", amount=300)
        line.with_user(self.approver).action_approve()
        self.assertEqual((line.state, line.move_id.state, line.approved_by_id), ("posted", "posted", self.approver))
        stock = self.voucher(description="salt", amount=900, product_id=self.salt.id, quantity=30)
        stock.with_user(self.approver).action_approve()
        self.assertEqual(stock.picking_id.state, "done")
        self.assertEqual(self.salt.qty_available, 30)
        topup = self.Line.browse(self.env["odin.petty.cash.float"].with_user(self.approver).action_add_float(
            self.flt.id, 1000, False, False, "2026-11-03"))
        self.assertEqual(topup.state, "posted")
        line.with_user(self.approver).action_reset()
        self.assertEqual((line.state, line.move_id.id), ("draft", False))

    def test_approved_vouchers_stay_approved(self):
        line = self.voucher()
        line.action_approve()
        as_cashier = line.with_user(self.cashier)
        for vals in ({"state": "draft"}, {"move_id": False}, {"voucher": "PCV/2000/01/001"}):
            with self.assertRaises(UserError, msg=vals):
                as_cashier.write(vals)
        with self.assertRaises(UserError):
            as_cashier.action_reset()
        with self.assertRaises(UserError):
            self.Line.with_user(self.cashier).create({"float_id": self.flt.id, "description": "x", "amount": 1,
                                                       "state": "posted"})
        for vals in ({"rule_id": self.rule_raw.id}, {"description": "something else"}, {"method": "cash"}):
            with self.assertRaises(UserError, msg=vals):
                line.write(vals)
        line.mpesa_code = "UK30R1H2AS"  # the code it lacked: allowed
        with self.assertRaises(UserError):
            line.mpesa_code = "UK30R1H2AT"
        self.assertEqual((line.state, line.rule_id), ("posted", self.rule_fuel))

    def test_approved_charge_stays_when_a_phone_is_added(self):
        line = self.voucher(amount=4800)
        line.action_approve()
        line.phone = "0723413614"
        self.assertEqual((line.charge, line.total), (0.0, 4800.0), "the charge its entry posted")

    # ------------------------------------------------------------ balances
    def test_cancelled_vouchers_do_not_move_the_balance(self):
        self.env["odin.petty.cash.float"].action_add_float(self.flt.id, 10000, False, False, "2026-11-01")
        self.voucher(amount=1000)
        self.voucher(amount=3000, date="2026-11-04").action_cancel()
        book = self.env["odin.petty.cash.float"].get_book(self.flt.id, 2026, 11)
        self.assertEqual((book["closing"], book["spent"]), (9000.0, 1000.0))
        self.assertEqual(book["rows"][-1]["balance"], 9000.0)
        self.assertEqual(book["months"][10]["count"], 2)

    def test_statement_check_counts_what_is_typed_up_to_its_day(self):
        self.env["odin.petty.cash.float"].action_add_float(self.flt.id, 1200, False, False, "2026-11-01")
        self.voucher(amount=200)  # typed, not approved yet
        self.voucher(amount=500, date="2026-11-20")  # after the statement
        self.upload(HEAD + "UK30R1H2AS,2026-11-03 10:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,1000.00\n")
        self.assertEqual(self.env["odin.petty.cash.float"].get_book(self.flt.id, 2026, 11)["float"]["statement_gap"], 0)

    # ------------------------------------------------------------ the statement
    def test_cashier_uploads_a_statement(self):
        typed = self.voucher(amount=200)
        self.upload(HEAD + "UK30R1H2AS,2026-11-03 10:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,1000.00\n",
                    user=self.cashier)
        self.assertEqual((typed.mpesa_code, typed.source), ("UK30R1H2AS", "matched"))
        self.assertEqual(self.flt.statement_balance, 1000.0)
        self.assertEqual(self.flt.statement_date, datetime(2026, 11, 3, 7, 30), "10:30 in Nairobi, kept in UTC")

    def test_each_voucher_answers_for_one_transaction(self):
        a, b = self.voucher(amount=200), self.voucher(amount=200)
        (a | b).action_approve()
        wizard = self.upload(HEAD
                             + "UK30R1H2AS,2026-11-03 10:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,1000.00\n"
                             + "UK30R1H2AT,2026-11-03 11:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,800.00\n"
                             + "UK30R1H2AU,2026-11-03 12:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,600.00\n")
        self.assertEqual({a.mpesa_code, b.mpesa_code}, {"UK30R1H2AS", "UK30R1H2AT"}, "approved ones get their codes")
        third = self.Line.search([("mpesa_code", "=", "UK30R1H2AU")])
        self.assertEqual((third.source, third.state), ("statement", "draft"), "the third payment is not swallowed")
        self.assertIn("1 new payments", wizard.result)

    def test_statement_brings_back_a_cancelled_voucher(self):
        line = self.voucher(amount=200, mpesa_code="UK30R1H2AS")
        line.action_cancel()
        wizard = self.upload(HEAD + "UK30R1H2AS,2026-11-03 10:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,1000.00\n")
        self.assertEqual(line.state, "draft")
        self.assertIn(line.voucher, wizard.result)

    def test_statement_reports_a_different_charge_on_an_approved_voucher(self):
        line = self.voucher(phone="0723413614", amount=4800)
        line.action_approve()
        wizard = self.upload(HEAD
                             + "UK30R1H2AS,2026-11-03 10:30:00,Customer Transfer to 0723***614 - X,Completed,,4800.00,1000.00\n"
                             + "UK30R1H2AS,2026-11-03 10:30:00,Customer Transfer of Funds Charge,Completed,,33.00,967.00\n")
        self.assertEqual(line.charge, 57.0, "its posted entry is not changed behind the approver's back")
        self.assertIn("57.00 on the voucher, 33.00 on the statement", wizard.result)
        self.assertEqual(self.flt.statement_balance, 967.0, "the balance after the charge")

    def test_an_older_statement_does_not_take_the_float_back(self):
        self.upload(HEAD + "UK30R1H2AS,2026-11-10 10:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,1000.00\n")
        wizard = self.upload(HEAD + "UK30R1H2AT,2026-11-03 10:30:00,Pay Bill to 400200 - SHELL,Completed,,200.00,5000.00\n")
        self.assertEqual(self.flt.statement_balance, 1000.0)
        self.assertIn("older than the last one", wizard.result)

    def test_balance_after_a_charge_printed_after_its_payment(self):
        txs = {t["code"]: t for t in group_rows(parse_pdf_text(PDF_TEXT))}
        self.assertEqual(txs["UK30R1F8QM"]["balance"], 11814.25, "charge printed after the payment")
        self.assertEqual(txs["UK30R1J9LE"]["balance"], 61603.0, "charge printed before it")

    # ------------------------------------------------------------ names
    def test_payee_names_are_names_not_patterns(self):
        self.env["res.partner"].create({"name": "AXB TRADERS"})
        line = self.voucher(paid_to="A_B TRADERS")
        self.assertFalse(line._find_partner(), "an underscore is a letter")

    def test_payees_stay_in_their_company(self):
        other = self.env["res.company"].create({"name": "Other Co"})
        journal = self.env["account.journal"].create({"name": "PC2", "code": "PC2", "type": "cash", "company_id": other.id})
        flt2 = self.env["odin.petty.cash.float"].create({"name": "Other", "journal_id": journal.id, "company_id": other.id})
        self.Line.create({"float_id": flt2.id, "date": "2026-11-03", "description": "secret",
                          "paid_to": "ZEBRA SECRET", "phone": "0711111111", "amount": 5})
        self.assertEqual(self.Line.with_user(self.cashier).book_suggest_payee(flt2.id, "zebra"), [])
        self.assertEqual(self.Line.with_user(self.cashier).book_suggest_description(flt2.id, "secret"), [])
