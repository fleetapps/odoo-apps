import base64
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .test_stock_book import TestStockBook

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n" \
      b"trailer<</Root 1 0 R>>\n%%EOF\n"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def b64(raw):
    return base64.b64encode(raw).decode()


@tagged("post_install", "-at_install")
class TestStockBookBill(TestStockBook):
    """Add bill: something not kept in stock, already paid. Odoo's vendor bill
    with lines on accounts, posted and paid in one step, and nothing on the
    book or in stock."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Rapid Printers Test"})
        Tax = cls.env["account.tax"]
        cls.vat_net = Tax.create({"name": "Test 16 net", "amount": 16, "type_tax_use": "purchase",
                                  "price_include_override": "tax_excluded"})
        cls.vat_in = Tax.create({"name": "Test 16 in", "amount": 16, "type_tax_use": "purchase",
                                 "price_include_override": "tax_included"})
        cls.vat_none = Tax.create({"name": "Test exempt", "amount": 0, "type_tax_use": "purchase"})
        cls.vat_account = cls.env["account.account"].create({
            "code": "111098", "name": "Test Bill VAT", "account_type": "asset_current"})
        for tax in cls.vat_net | cls.vat_in:
            tax.invoice_repartition_line_ids.filtered(lambda r: r.repartition_type == "tax").account_id = cls.vat_account
        cls.outstanding = cls.env["account.account"].create({
            "code": "120098", "name": "Test Outstanding Bill Payments", "account_type": "asset_current",
            "reconcile": True})
        cls.pay_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1)
        cls.pay_journal.outbound_payment_method_line_ids[:1].payment_account_id = cls.outstanding
        Account = cls.env["account.account"]
        cls.stationery = Account.create({"code": "512198", "name": "Test Stationery", "account_type": "expense"})
        cls.power = Account.create({"code": "511398", "name": "Test Light and power", "account_type": "expense"})
        cls.printer = Account.create({"code": "003098", "name": "Test Office equipment", "account_type": "asset_fixed"})
        cls.today = fields.Date.context_today(cls.Book)

    def _bill(self, **values):
        return self.Book.book_bill_save({
            "partner_id": self.vendor.id, "purchase_ref": "INV-2001321", "date": fields.Date.to_string(self.today),
            "pay_journal_id": self.pay_journal.id,
            "lines": [{"name": "A4 printing paper, 10 reams", "account_id": self.stationery.id, "qty": 10,
                       "cost": 1000, "tax_id": self.vat_net.id}],
            **values,
        })

    def _move(self, result):
        return self.env["account.move"].browse(result["bill_id"])

    # ------------------------------------------------------------ the bill
    def test_a_bill_is_posted_paid_and_adds_no_stock(self):
        moves, lines = self.env["stock.move"].search_count([]), self.Line.search_count([])
        orders = self.env["purchase.order"].search_count([])
        result = self._bill()
        bill = self._move(result)
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.journal_id.type, "purchase")
        self.assertEqual(bill.partner_id, self.vendor)
        self.assertEqual(bill.ref, "INV-2001321", "the number on the paper is the bill reference")
        self.assertEqual(bill.invoice_date, self.today)
        self.assertEqual(bill.amount_total, 11600)
        self.assertIn(bill.payment_state, ("in_payment", "paid"))
        self.assertFalse(bill.invoice_line_ids.product_id, "no product")
        self.assertEqual(self.env["stock.move"].search_count([]), moves, "nothing moves in stock")
        self.assertEqual(self.Line.search_count([]), lines, "nothing on the book")
        self.assertEqual(self.env["purchase.order"].search_count([]), orders, "no purchase order")
        self.assertEqual(result["bill"], bill.name)
        self.assertEqual(result["total"], 11600)
        self.assertEqual(result["paid_from"], self.pay_journal.display_name)

    def test_each_line_keeps_what_it_was_for_its_account_and_its_vat(self):
        bill = self._move(self._bill(lines=[
            {"name": "A4 printing paper, 10 reams", "account_id": self.stationery.id, "qty": 10, "cost": 1000,
             "tax_id": self.vat_net.id},
            {"name": "Electricity, November", "account_id": self.power.id, "qty": 1, "cost": 5000,
             "tax_id": self.vat_none.id},
        ]))
        paper, power = bill.invoice_line_ids.sorted("sequence")
        self.assertEqual((paper.name, paper.account_id, paper.tax_ids, paper.quantity, paper.price_unit),
                         ("A4 printing paper, 10 reams", self.stationery, self.vat_net, 10, 1000))
        self.assertEqual((power.name, power.account_id, power.tax_ids, power.price_subtotal),
                         ("Electricity, November", self.power, self.vat_none, 5000))
        self.assertEqual(bill.amount_untaxed, 15000)
        self.assertEqual(bill.amount_tax, 1600, "VAT on the paper only")

    def test_the_bill_books_the_expense_the_vat_and_the_supplier_and_the_payment_clears_it(self):
        bill = self._move(self._bill())
        lines = bill.line_ids
        self.assertEqual(sum(lines.filtered(lambda l: l.account_id == self.stationery).mapped("debit")), 10000)
        self.assertEqual(sum(lines.filtered(lambda l: l.account_id == self.vat_account).mapped("debit")), 1600)
        payable = lines.filtered(lambda l: l.account_id.account_type == "liability_payable")
        self.assertEqual(sum(payable.mapped("credit")), 11600)
        self.assertEqual(payable.partner_id, self.vendor)
        self.assertTrue(payable.reconciled, "settled by the payment")
        self.assertEqual(bill.amount_residual, 0)
        payment = self.env["account.payment"].search([("partner_id", "=", self.vendor.id)])
        self.assertEqual(payment.journal_id, self.pay_journal)
        self.assertEqual(payment.amount, 11600)
        self.assertEqual(payment.date, self.today)
        self.assertEqual(sum(payment.move_id.line_ids.filtered(
            lambda l: l.account_id == self.outstanding).mapped("credit")), 11600)

    def test_totals_preview_equals_the_bill(self):
        rows = [{"qty": 10, "cost": 1000, "tax_id": self.vat_net.id}, {"qty": 1, "cost": 1160, "tax_id": self.vat_in.id}]
        totals = self.Book.book_bill_totals(rows, partner_id=self.vendor.id)
        bill = self._move(self._bill(lines=[
            {"name": "Paper", "account_id": self.stationery.id, **rows[0]},
            {"name": "Toner, price with VAT", "account_id": self.stationery.id, **rows[1]},
        ]))
        self.assertEqual(totals["subtotal"], bill.amount_untaxed)
        self.assertEqual(totals["vat"], bill.amount_tax)
        self.assertEqual(totals["total"], bill.amount_total)
        self.assertEqual(totals["lines"][0], {"subtotal": 10000, "vat": 1600, "total": 11600})
        self.assertEqual(totals["lines"][1], {"subtotal": 1000, "vat": 160, "total": 1160},
                         "a VAT included in the price reads the price as the total")
        self.assertEqual(sum(t["amount"] for t in totals["taxes"]), 1760)

    def test_back_dated_bill_lands_on_its_day(self):
        day = self.today - timedelta(days=5)
        bill = self._move(self._bill(date=fields.Date.to_string(day)))
        self.assertEqual(bill.invoice_date, day)
        self.assertEqual(bill.date, day)
        self.assertEqual(self.env["account.payment"].search([("partner_id", "=", self.vendor.id)]).date, day)

    def test_a_bill_on_a_closed_day_goes_to_the_first_open_one(self):
        day = self.today - timedelta(days=40)
        self.env.company.purchase_lock_date = self.today - timedelta(days=20)
        bill = self._move(self._bill(date=fields.Date.to_string(day)))
        self.assertEqual(bill.invoice_date, day, "the paper's date stays the bill date")
        self.assertGreater(bill.date, self.env.company.purchase_lock_date, "in the accounts after the lock date")
        self.assertEqual(bill.state, "posted")

    def test_qty_left_empty_is_one(self):
        bill = self._move(self._bill(lines=[{"name": "Repair", "account_id": self.stationery.id, "qty": None,
                                             "cost": 2500, "tax_id": self.vat_net.id}]))
        self.assertEqual(bill.invoice_line_ids.quantity, 1)
        self.assertEqual(bill.amount_untaxed, 2500)

    # ------------------------------------------------------------ the scan
    def test_the_scan_shows_beside_the_bill_and_changes_nothing_on_it(self):
        bill = self._move(self._bill(files=[{"name": "bill.pdf", "data": b64(PDF)},
                                            {"name": "photo.png", "data": b64(PNG)}]))
        attachments = self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", bill.id)])
        self.assertEqual(sorted(attachments.mapped("name")), ["bill.pdf", "photo.png"])
        self.assertEqual(bill.message_main_attachment_id.name, "bill.pdf", "the PDF is shown beside the bill")
        note = bill.message_ids.filtered(lambda m: m.attachment_ids)
        self.assertEqual(note.attachment_ids, attachments, "in the chatter, with the note")
        self.assertEqual(len(bill.invoice_line_ids), 1, "nothing was read from the scan into the bill")
        self.assertEqual(bill.amount_total, 11600)

    def test_a_file_that_is_not_a_pdf_or_photo_is_refused_and_nothing_is_saved(self):
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(files=[{"name": "bill.pdf", "data": b64(b"just some words, not a scan")}])
        self.assertFalse(self.env["account.move"].search([("ref", "=", "INV-2001321")]))

    def test_too_many_or_too_large_files_are_refused(self):
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(files=[{"name": f"p{n}.png", "data": b64(PNG)} for n in range(6)])
        with patch("odoo.addons.odin_stock_book.models.stock_bill.BILL_FILE_MAX_MB", 0), \
                self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(files=[{"name": "big.png", "data": b64(PNG)}])
        self.assertFalse(self.env["account.move"].search([("ref", "=", "INV-2001321")]))

    # ------------------------------------------------------------ numbers and the CU number
    def test_a_number_goes_in_once_per_supplier(self):
        self._bill()
        self.assertTrue(self.Book.book_bill_check(self.vendor.id, "inv-2001321"))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(purchase_ref="inv-2001321")
        other = self.env["res.partner"].create({"name": "Other Supplier"})
        self.assertFalse(self.Book.book_bill_check(other.id, "INV-2001321"), "another supplier's number")
        self._bill(partner_id=other.id)

    def test_the_cu_number_goes_on_the_bill_and_is_not_used_twice(self):
        bill = self._move(self._bill(cu_invoice_no=" KRACU0100000001/152 "))
        self.assertEqual(bill.ke_etims_supplier_invoice_ref, "KRACU0100000001/152")
        self.assertTrue(self.Book.book_bill_cu_check("kracu0100000001/152"))
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(purchase_ref="INV-2", cu_invoice_no="kracu0100000001/152")
        self.assertFalse(self.Book.book_bill_cu_check("KRACU0100000001/153"))

    def test_the_cu_number_can_be_added_on_the_bill_later(self):
        bill = self._move(self._bill())
        self.assertFalse(bill.ke_etims_supplier_invoice_ref)
        bill.ke_etims_supplier_invoice_ref = "KRACU0100000001/154"
        self.assertEqual(bill.state, "posted")
        arch = self.env["account.move"].get_views([(False, "form")])["views"]["form"]["arch"]
        self.assertIn('name="ke_etims_supplier_invoice_ref"', arch, "on the bill form")

    # ------------------------------------------------------------ the supplier
    def test_a_new_supplier_by_name_with_its_pin(self):
        result = self.Book.book_bill_save({
            "partner_name": "First Time Supplier", "partner_vat": "p05123 4567x", "purchase_ref": "R-1",
            "date": fields.Date.to_string(self.today), "pay_journal_id": self.pay_journal.id,
            "lines": [{"name": "Water", "account_id": self.stationery.id, "qty": 1, "cost": 300,
                       "tax_id": self.vat_none.id}]})
        partner = self._move(result).partner_id
        self.assertEqual(partner.name, "First Time Supplier")
        self.assertEqual(partner.vat, "P051234567X", "as KRA writes it")
        self.assertEqual(partner.supplier_rank, 1)

    def test_a_pin_typed_goes_on_the_supplier_and_must_be_a_kra_pin(self):
        self._bill(partner_vat="P051234567X")
        self.assertEqual(self.vendor.vat, "P051234567X")
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(purchase_ref="INV-2", partner_vat="12345")
        self.vendor.vat = "GB123456789"
        self._bill(purchase_ref="INV-3", partner_vat="GB123456789")
        self.assertEqual(self.vendor.vat, "GB123456789", "a foreign tax ID on file stays")
        self._bill(purchase_ref="INV-4", partner_vat="")
        self.assertEqual(self.vendor.vat, "GB123456789", "an empty box takes nothing off the supplier")

    def test_the_suppliers_usual_account_and_vat_start_its_lines(self):
        self._bill()
        defaults = self.Book.book_bill_vendor(self.vendor.id)
        self.assertEqual(defaults["account"]["id"], self.stationery.id)
        self.assertEqual(defaults["tax_id"], self.vat_net.id)
        first, *others = self.Book.book_bill_accounts("", partner_id=self.vendor.id)
        self.assertEqual(first["id"], self.stationery.id, "what this supplier's bills use, first")
        self.assertTrue(first["used"])
        self.assertTrue(others)
        self.assertFalse([a["label"] for a in others if a["used"]], "only what its bills used is marked used")
        found = self.Book.book_bill_accounts("test stat")
        self.assertEqual([a["id"] for a in found], [self.stationery.id], "found by words of its code or name")

    def test_a_line_starts_on_its_accounts_default_vat(self):
        self.power.tax_ids = self.vat_net
        found = self.Book.book_bill_accounts("test light")
        self.assertEqual(found[0]["tax_id"], self.vat_net.id)

    # ------------------------------------------------------------ what it can be charged to
    def test_stock_and_control_accounts_are_not_offered(self):
        company = self.env.company
        if not company.account_stock_valuation_id:
            company.account_stock_valuation_id = self.env["account.account"].create(
                {"code": "100198", "name": "Test Stock", "account_type": "asset_current"})
        offered = self.Book._bill_accounts()
        self.assertIn(self.stationery, offered)
        self.assertIn(self.printer, offered, "equipment paid for now")
        self.assertNotIn(company.account_stock_valuation_id, offered)
        self.assertFalse(offered.filtered(lambda a: a.account_type in (
            "asset_receivable", "liability_payable", "asset_cash", "income", "equity", "liability_current")))
        self.assertNotIn(self.vat_account, offered)
        self.assertNotIn(self.outstanding, offered)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(lines=[{"name": "Paper", "account_id": company.account_stock_valuation_id.id, "qty": 1,
                               "cost": 100, "tax_id": self.vat_net.id}])

    def test_each_line_needs_what_for_an_account_an_amount_and_its_vat(self):
        good = {"name": "Paper", "account_id": self.stationery.id, "qty": 1, "cost": 100, "tax_id": self.vat_net.id}
        for wrong in ({"name": " "}, {"account_id": False}, {"cost": 0}, {"qty": -1}, {"tax_id": False}):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self._bill(lines=[{**good, **wrong}])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(lines=[])
        self.assertFalse(self.env["account.move"].search([("ref", "=", "INV-2001321")]))

    def test_the_header_needs_a_number_a_day_not_ahead_and_where_it_was_paid_from(self):
        for wrong in ({"purchase_ref": " "}, {"date": False},
                      {"date": fields.Date.to_string(self.today + timedelta(days=1))},
                      {"pay_journal_id": False}, {"partner_id": False}):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self._bill(**wrong)

    # ------------------------------------------------------------ paying
    def test_it_is_paid_from_a_journal_that_reaches_the_ledger(self):
        self.assertIn(self.pay_journal["id"], [j["id"] for j in self.Book.book_bill_options()["journals"]])
        float_journal = self.env["account.journal"].create({"name": "Float", "code": "FLT8", "type": "cash"})
        float_journal.outbound_payment_method_line_ids.payment_account_id = False
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self._bill(pay_journal_id=float_journal.id)

    def test_nothing_stays_if_the_payment_fails(self):
        Book = type(self.env["odin.stock.book"])
        with patch.object(Book, "_bill_pay", side_effect=UserError("No")):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self._bill(files=[{"name": "bill.pdf", "data": b64(PDF)}])
        self.assertFalse(self.env["account.move"].search([("ref", "=", "INV-2001321")]))
        self.assertFalse(self.env["ir.attachment"].search([("name", "=", "bill.pdf"), ("res_model", "=", "account.move")]))

    # ------------------------------------------------------------ who
    def test_a_storekeeper_enters_a_bill_without_accounting_rights(self):
        keeper = new_test_user(self.env, "bill_keeper", groups="odin_stock_book.group_stock_book_user")
        self.assertFalse(keeper.has_group("account.group_account_invoice"))
        Book = self.Book.with_user(keeper)
        self.assertTrue(Book.book_bill_accounts("test stat"))
        result = Book.book_bill_save({
            "partner_id": self.vendor.id, "purchase_ref": "INV-K1", "date": fields.Date.to_string(self.today),
            "pay_journal_id": self.pay_journal.id, "files": [{"name": "bill.pdf", "data": b64(PDF)}],
            "lines": [{"name": "Paper", "account_id": self.stationery.id, "qty": 1, "cost": 100,
                       "tax_id": self.vat_net.id}]})
        bill = self._move(result)
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.create_uid, keeper, "recorded as theirs")
        self.assertEqual(bill.message_ids.filtered("attachment_ids").author_id, keeper.partner_id)
        self.assertFalse(result["can_open"], "no accounting rights: no button to open it")

    def test_only_stock_book_users_enter_bills(self):
        outsider = new_test_user(self.env, "bill_outsider", groups="base.group_user")
        Book = self.Book.with_user(outsider)
        with self.assertRaises(AccessError):
            Book.book_bill_save({"partner_id": self.vendor.id})
        with self.assertRaises(AccessError):
            Book.book_bill_accounts("")
