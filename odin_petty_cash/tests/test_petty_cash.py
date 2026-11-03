import base64

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestPettyCash(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Account = cls.env["account.account"]
        cls.float_account = Account.create({"name": "Petty Cash - M-Pesa Float", "code": "120097",
                                            "account_type": "asset_cash"})
        cls.journal = cls.env["account.journal"].create({
            "name": "Petty Cash", "code": "PCT", "type": "cash", "default_account_id": cls.float_account.id})
        cls.fuel = Account.create({"name": "Car fuel", "code": "511697", "account_type": "expense"})
        cls.raw = Account.create({"name": "Raw materials petty", "code": "500197", "account_type": "expense"})
        cls.charges = Account.create({"name": "Bank charges", "code": "513897", "account_type": "expense"})
        cls.director = Account.create({"name": "Director", "code": "210797", "account_type": "liability_current"})
        Rule = cls.env["account.reconcile.model"]
        cls.rule_fuel = Rule.create({
            "name": "Auto: Fuel - KDU 865J", "sequence": 20, "match_label": "match_regex",
            "match_label_param": "(fuel|petrol).*(kdu)", "match_journal_ids": [Command.set(cls.journal.ids)],
            "line_ids": [Command.create({"account_id": cls.fuel.id, "amount_type": "percentage", "amount_string": "100"})]})
        cls.rule_raw = Rule.create({
            "name": "Auto: Raw materials", "sequence": 10, "match_label": "match_regex",
            "match_label_param": "(salt|perfume)", "match_journal_ids": [Command.set(cls.journal.ids)],
            "line_ids": [Command.create({"account_id": cls.raw.id, "amount_type": "percentage", "amount_string": "100"})]})
        cls.rule_topup = Rule.create({
            "name": "Auto: Float top-up", "sequence": 5, "match_label": "match_regex",
            "match_label_param": "(top ?up|float)", "match_journal_ids": [Command.set(cls.journal.ids)],
            "line_ids": [Command.create({"account_id": cls.director.id, "amount_type": "percentage", "amount_string": "100"})]})
        cls.flt = cls.env["odin.petty.cash.float"].create({
            "name": "M-Pesa float", "journal_id": cls.journal.id, "charge_account_id": cls.charges.id,
            "stock_rule_ids": [Command.set(cls.rule_raw.ids)]})
        categ = cls.env["product.category"].create({"name": "Raw materials (FIFO)", "property_cost_method": "fifo"})
        cls.salt = cls.env["product.product"].create({"name": "Fine Salt", "is_storable": True, "type": "consu",
                                                     "categ_id": categ.id})
        cls.env.user.group_ids += cls.env.ref("odin_petty_cash.group_petty_cash_approver")
        cls.Line = cls.env["odin.petty.cash.line"]

    def test_rule_charge_voucher_and_post(self):
        line = self.Line.create({"float_id": self.flt.id, "date": "2026-11-03", "description": "fuel kdu 865j",
                                 "paid_to": "shell", "phone": "0723413614", "amount": 4800})
        self.assertEqual(line.voucher, "PCV/2026/11/001")
        self.assertEqual(line.description, "FUEL KDU 865J")
        self.assertEqual(line.rule_id, self.rule_fuel)
        self.assertEqual(line.account_id, self.fuel)
        self.assertEqual(line.charge, 57.0, "send-money tariff")
        self.assertEqual(line.status_key, "ready")
        line.action_approve()
        self.assertEqual(line.state, "posted")
        move = line.move_id
        self.assertEqual(move.state, "posted")
        self.assertEqual(sorted(move.line_ids.mapped("debit")), [0.0, 57.0, 4800.0])
        self.assertEqual(move.line_ids.filtered(lambda l: l.account_id == self.float_account).credit, 4857.0)
        with self.assertRaises(UserError):
            line.amount = 10
        line.action_reset()
        self.assertEqual(line.state, "draft")
        more = self.Line.create([{"float_id": self.flt.id, "date": "2026-11-04", "description": "x", "amount": 1},
                                 {"float_id": self.flt.id, "date": "2026-11-05", "description": "y", "amount": 1}])
        self.assertEqual(more.mapped("voucher"), ["PCV/2026/11/002", "PCV/2026/11/003"])

    def test_status_progression_and_cash_receipt(self):
        line = self.Line.create({"float_id": self.flt.id, "date": "2026-11-03", "method": "cash"})
        self.assertEqual(line.status_key, "need_amount")
        line.amount = 2000
        self.assertEqual(line.status_key, "need_description")
        line.description = "something odd"
        self.assertEqual(line.status_key, "need_category")
        line.account_id = self.fuel
        self.assertEqual(line.status_key, "need_receipt")
        line.book_attach("receipt.png", base64.b64encode(b"fake").decode())
        self.assertEqual(line.status_key, "ready")
        self.assertEqual(line.charge, 0.0)

    def test_stock_purchase_received_at_cost(self):
        line = self.Line.create({"float_id": self.flt.id, "date": "2026-11-03", "description": "fine salt 20 bags",
                                 "paid_to": "kensalt", "amount": 19000})
        self.assertTrue(line.needs_stock)
        self.assertEqual(line.status_key, "need_stock")
        line.write({"product_id": self.salt.id, "quantity": 1000})
        self.assertEqual(line.status_key, "ready")
        line.action_approve()
        self.assertEqual(line.picking_id.state, "done")
        self.assertEqual(self.salt.qty_available, 1000)
        self.assertAlmostEqual(self.salt.standard_price, 19.0)

    def test_topup_and_book(self):
        line_id = self.env["odin.petty.cash.float"].action_add_float(self.flt.id, 50000, False, "UK30R1HQ0Z", "2026-11-03")
        topup = self.Line.browse(line_id)
        self.assertEqual(topup.state, "posted")
        self.assertEqual(topup.account_id, self.director)
        self.Line.create({"float_id": self.flt.id, "date": "2026-11-03", "description": "fuel kdu",
                          "amount": 500, "mpesa_code": "UK30R1F8QM"})
        book = self.env["odin.petty.cash.float"].get_book(self.flt.id, 2026, 11)
        self.assertEqual(book["rows"][-1]["balance"], 50000 - 500)
        self.assertEqual(book["counts"]["ready"], 1)
        self.assertEqual(book["months"][10]["count"], 2)
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.Line.create({"float_id": self.flt.id, "description": "dup", "amount": 1, "mpesa_code": "uk30r1f8qm"})

    def test_statement_import_matches_typed_row(self):
        typed = self.Line.create({"float_id": self.flt.id, "date": "2026-11-03", "description": "parking kdd",
                                  "paid_to": "paul", "amount": 200})
        csv_text = ("Receipt No.,Completion Time,Details,Transaction Status,Paid In,Withdrawn,Balance\n"
                    "UK30R1H2AS,2026-11-03 10:30:00,Customer Transfer to 0723***614 - PAUL LUNYASI,Completed,,200.00,1000.00\n"
                    "UK30R1H2AS,2026-11-03 10:30:00,Customer Transfer of Funds Charge,Completed,,7.00,993.00\n"
                    "UK30R1J9LE,2026-11-03 11:20:00,Merchant Payment to 5544 - NAIVAS,Completed,,1240.00,-247.00\n")
        wizard = self.env["odin.petty.cash.import"].create({
            "float_id": self.flt.id, "filename": "statement.csv",
            "statement_file": base64.b64encode(csv_text.encode())})
        wizard.action_import()
        self.assertEqual(typed.mpesa_code, "UK30R1H2AS")
        self.assertEqual(typed.charge, 7.0)
        self.assertEqual(typed.source, "matched")
        new = self.Line.search([("mpesa_code", "=", "UK30R1J9LE")])
        self.assertEqual((new.paid_to, new.amount, new.status_key), ("NAIVAS", 1240.0, "need_description"))
        self.assertEqual(self.flt.statement_balance, -247.0)

    def test_code_format_and_duplicate_hint(self):
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.Line.create({"float_id": self.flt.id, "description": "x", "amount": 1, "mpesa_code": "0716105758X"})
        first = self.Line.book_save(False, {"float_id": self.flt.id, "date": "2026-11-03", "description": "milk",
                                            "paid_to": "mary", "amount": 200})
        self.assertFalse(first["duplicate_of"])
        again = self.Line.book_save(False, {"float_id": self.flt.id, "date": "2026-11-03", "description": "milk 2",
                                            "paid_to": "Mary", "amount": 200})
        self.assertEqual(again["duplicate_of"], first["voucher"])

    def test_rule_falls_back_to_payee(self):
        line = self.Line.create({"float_id": self.flt.id, "description": "20 bags", "paid_to": "kensalt salt",
                                 "amount": 950})
        self.assertEqual(line.rule_id, self.rule_raw)

    def test_payee_suggestions(self):
        for _i in range(3):
            self.Line.create({"float_id": self.flt.id, "description": "milk", "paid_to": "naivas imaara", "amount": 100})
        found = self.Line.book_suggest_payee(self.flt.id, "naiv")
        self.assertEqual(found[0]["paid_to"], "NAIVAS IMAARA")
        self.assertEqual(found[0]["usual"], "MILK")
