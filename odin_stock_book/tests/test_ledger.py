from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from .test_purchase import TestStockBookPurchase


@tagged("post_install", "-at_install")
class TestStockBookLedger(TestStockBookPurchase):
    """Past stock brought into the accounts, and new stock that gets there."""

    def _imported_order(self, qty=10, price=100.0):
        """As Rinz's imported orders are: confirmed, its receipt cancelled
        because the stock came in by a count, no bill."""
        order = self.env["purchase.order"].create({
            "partner_id": self.supplier.id, "partner_ref": "IMP-1", "date_order": "2026-03-05 09:00:00",
            "origin": "2026_PURCHASES March rows 1-1",
            "order_line": [Command.create({
                "product_id": self.bottle.id, "product_qty": qty, "price_unit": price,
                "tax_ids": [Command.set(self.vat_net.ids)]})],
        })
        order.button_confirm()
        order.picking_ids.action_cancel()
        return order

    # ------------------------------------------------------------ the imported orders
    def test_dry_run_reports_and_changes_nothing(self):
        order = self._imported_order()
        report = self.Book.book_bill_imported()
        self.assertEqual(report["orders"], 1)
        self.assertEqual(report["total"], 1160)
        self.assertFalse(order.invoice_ids)
        self.assertEqual(order.order_line.qty_received_method, "stock_moves")

    def test_bill_adds_no_stock_and_books_stock_vat_and_payable(self):
        order = self._imported_order()
        onhand = self.onhand(self.bottle)
        value = self.bottle.total_value
        report = self.Book.book_bill_imported(dry_run=False)
        self.assertEqual(report["failed"], [])
        bill = order.invoice_ids
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.invoice_date, fields.Date.to_date("2026-03-05"), "dated as the order")
        self.assertEqual(bill.ref, "IMP-1")
        self.assertEqual(bill.amount_untaxed, 1000)
        self.assertEqual(bill.amount_tax, 160)
        self.assertEqual(self.onhand(self.bottle), onhand, "no stock is added twice")
        self.bottle.invalidate_recordset()
        self.assertEqual(self.bottle.total_value, value, "nor is the stock revalued")
        self.assertEqual(order.picking_ids.mapped("state"), ["cancel"], "no new receipt")
        self.assertEqual(order.invoice_status, "invoiced", "not left under To bill")
        stock = bill.invoice_line_ids.account_id
        accounts = self.bottle.product_tmpl_id.get_product_accounts()
        if self.bottle.valuation == "real_time":
            self.assertEqual(stock, accounts["stock_valuation"])
        self.assertEqual(self.Book._imported_orders(), self.env["purchase.order"], "billed once")

    def test_paid_from_the_bank_on_the_day_of_the_bill(self):
        order = self._imported_order()
        self.Book.book_bill_imported(dry_run=False, pay_journal_id=self.pay_journal.id)
        bill = order.invoice_ids
        self.assertIn(bill.payment_state, ("in_payment", "paid"))
        payment = bill._get_reconciled_payments()
        self.assertEqual(payment.journal_id, self.pay_journal)
        self.assertEqual(payment.date, bill.invoice_date)

    def test_only_the_orders_named_are_billed(self):
        first, second = self._imported_order(), self._imported_order()
        second.partner_ref = "IMP-2"
        self.Book.book_bill_imported(dry_run=False, order_ids=first.ids)
        self.assertTrue(first.invoice_ids)
        self.assertFalse(second.invoice_ids)

    def test_a_vat_with_no_account_is_not_offered(self):
        tax = self.env["account.tax"].create({"name": "Test no account", "amount": 16, "type_tax_use": "purchase"})
        self.assertNotIn(tax, self.Book._purchase_taxes())

    def test_partial_orders_wait_until_asked(self):
        order = self._imported_order()
        order.origin = "2026_PURCHASES March rows 1-3 PARTIAL 2 line(s) awaiting product"
        report = self.Book.book_bill_imported(dry_run=False)
        self.assertEqual(report["to_bill"], 0)
        self.assertFalse(order.invoice_ids)
        self.Book.book_bill_imported(dry_run=False, include_partial=True)
        self.assertTrue(order.invoice_ids)

    def test_its_number_stays_refused_once_billed(self):
        self._imported_order()
        self.Book.book_bill_imported(dry_run=False)
        self.assertTrue(self.Book.book_purchase_check(self.supplier.id, "IMP-1"))

    def test_needs_an_accounting_administrator(self):
        user = self.env["res.users"].create({"name": "Clerk", "login": "clerk_ledger", "group_ids": [
            Command.set(self.env.ref("base.group_user").ids)]})
        with self.assertRaises(UserError):
            self.Book.with_user(user).book_bill_imported()

    # ------------------------------------------------------------ setup and closing
    def test_setup_makes_scrap_post_to_the_ledger(self):
        if self.bottle.valuation != "real_time":
            self.bottle.categ_id.property_valuation = "real_time"
        company = self.env.company
        company.account_stock_valuation_id = self.bottle.product_tmpl_id.get_product_accounts()["stock_valuation"]
        company.account_stock_journal_id = company.account_stock_journal_id or self.env["account.journal"].search(
            [("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
        self.Book.book_ledger_setup(dry_run=False)
        location = self.Book._inventory_location()
        self.assertEqual(location.valuation_account_id.code, "500106")
        self.assertTrue(self.env.ref("odin_stock_book.ir_cron_stock_book_close").active)
        line = self.Line.create({"product_id": self.bottle.id, "reason": "spoiled", "qty_out": 1})
        line.action_post()
        self.assertTrue(line.scrap_id.move_ids.account_move_id or location != line.scrap_id.scrap_location_id,
                        "a scrap to the location with an account has its entry")

    def test_closing_preview_posts_nothing(self):
        before = self.env["account.move"].search_count([])
        report = self.Book.book_stock_closing()
        self.assertIn("stock_value", report)
        self.assertEqual(self.env["account.move"].search_count([]), before)

    # ------------------------------------------------------------ sales
    def _sale_values(self, **more):
        tax = self.Book._sale_taxes()[:1] or self.env["account.tax"].create(
            {"name": "Test sale 16", "amount": 16, "type_tax_use": "sale"})
        customer = self.env["res.partner"].create({"name": "Naivas Test"})
        return {"partner_id": customer.id, "purchase_ref": "INV-1", "date": fields.Date.to_string(fields.Date.today()),
                "warehouse_id": self.wh.id, "pay_journal_id": False,
                "lines": [{"product_id": self.fg.id, "qty": 2, "cost": 500.0, "tax_id": tax.id}], **more}

    def test_sale_makes_order_delivery_invoice_and_marks_the_customer(self):
        before = self.onhand(self.fg)
        values = self._sale_values()
        result = self.Book.book_sale_save(values)
        self.assertTrue(result["posted"])
        line = self.Line.browse(result["ids"])
        order = line.stock_sale_id.order_id
        self.assertEqual(order.state, "sale")
        self.assertEqual(order.client_order_ref, "INV-1")
        self.assertEqual(line.picking_id.state, "done")
        self.assertEqual(self.onhand(self.fg), before - 2)
        invoice = line.invoice_id
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.amount_untaxed, 1000)
        self.assertGreater(order.partner_id.customer_rank, 0, "shows under Sales > Customers")
        self.assertEqual(order.order_line.qty_delivered, 2)
        self.assertEqual(self.Line.search_count([("sale_line_id", "=", order.order_line.id)]), 1,
                         "the book's own delivery is not logged twice")

    def test_a_price_typed_over_the_pricelist_is_what_the_customer_is_invoiced(self):
        self.env.user.group_ids += self.env.ref("product.group_product_pricelist")
        pricelist = self.env["product.pricelist"].create({"name": "Chandarana", "item_ids": [
            Command.create({"applied_on": "1_product", "product_tmpl_id": self.fg.product_tmpl_id.id,
                            "compute_price": "fixed", "fixed_price": 120})]})
        values = self._sale_values()
        customer = self.env["res.partner"].browse(values["partner_id"])
        customer.property_product_pricelist = pricelist
        self.assertEqual(self.Book.book_sale_item(self.fg.id, customer.id, 2)["price"], 120.0)
        values["lines"][0]["cost"] = 110.0
        line = self.Line.browse(self.Book.book_sale_save(values)["ids"])
        order = line.stock_sale_id.order_id
        self.assertEqual(order.pricelist_id, pricelist)
        self.assertEqual(order.order_line.price_unit, 110.0, "the order keeps the price typed, not the pricelist's")
        self.assertEqual(line.invoice_id.invoice_line_ids.price_unit, 110.0)

    def test_free_sale_delivers_without_an_invoice(self):
        values = self._sale_values()
        values["lines"][0]["cost"] = 0
        result = self.Book.book_sale_save(values)
        line = self.Line.browse(result["ids"])
        self.assertEqual(line.picking_id.state, "done")
        self.assertFalse(line.invoice_id)
        self.assertGreater(line.stock_sale_id.order_id.partner_id.customer_rank, 0)

    def test_sale_number_is_not_used_twice(self):
        values = self._sale_values()
        self.Book.book_sale_save(values)
        self.assertTrue(self.Book.book_sale_check(values["partner_id"], "inv-1"))
        with self.assertRaises(UserError):
            self.Book.book_sale_save(values)

    def test_inline_sale_asks_for_its_invoice_once_required(self):
        line = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 1, "partner_name": "NAIVAS TEST"})
        self.assertEqual(line._status()[0], "ready", "off until turned on")
        self.env.company.odin_sale_needs_invoice = True
        line.invalidate_recordset()
        self.assertEqual(line._status()[0], "need_invoice")
        self.assertEqual(self.Book.book_sale_load(line.id)["replace_ids"], line.ids)

    # ------------------------------------------------------------ the customer's PIN and terms, the CU number
    def test_the_sale_window_offers_the_payment_terms_and_dates_the_invoice_due(self):
        terms = self.env.ref("account.account_payment_term_30days")
        self.assertIn(terms.id, [t["id"] for t in self.Book.book_sale_options()["payment_terms"]])
        self.assertEqual(self.Book.book_sale_due(terms.id, "2026-11-09"), "2026-12-09")
        self.assertEqual(self.Book.book_sale_due(False, "2026-11-09"), "2026-11-09", "no terms: due on the day")

    def test_a_sale_puts_the_pin_and_terms_on_the_customer_and_the_cu_number_on_the_invoice(self):
        terms = self.env.ref("account.account_payment_term_30days")
        day = fields.Date.today() - timedelta(days=10)
        values = self._sale_values(date=fields.Date.to_string(day), partner_vat=" p052112729p ",
                                   payment_term_id=terms.id, cu_invoice_no=" KRACU0100012345/77 ")
        customer = self.env["res.partner"].browse(values["partner_id"])
        self.assertFalse(customer.vat or customer.property_payment_term_id)
        result = self.Book.book_sale_save(values)
        self.assertEqual(customer.vat, "P052112729P", "kept on the customer, as KRA writes it")
        self.assertEqual(customer.property_payment_term_id, terms)
        sale = self.Line.browse(result["ids"]).stock_sale_id
        self.assertEqual(sale.cu_invoice_no, "KRACU0100012345/77")
        self.assertEqual(sale.order_id.payment_term_id, terms)
        invoice = sale.invoice_id
        self.assertEqual(invoice.invoice_payment_term_id, terms)
        self.assertEqual(invoice.invoice_date, day)
        self.assertEqual(invoice.invoice_date_due, day + timedelta(days=30), "from the invoice date, not today")
        self.assertEqual(result["due"], fields.Date.to_string(day + timedelta(days=30)))
        if "doc_cu_invoice_no" in invoice._fields:  # Business Documents installed: what the invoice prints
            self.assertEqual(invoice.doc_tax_invoice_no, "INV-1")
            self.assertEqual(invoice.doc_cu_invoice_no, "KRACU0100012345/77")
        found = {c["id"]: c for c in self.Book.book_sale_customers("Naivas Test")["items"]}
        self.assertEqual((found[customer.id]["vat"], found[customer.id]["payment_term_id"]),
                         ("P052112729P", terms.id), "the next sale to them opens with both")

    def test_a_sale_without_terms_is_due_on_its_invoice_date(self):
        day = fields.Date.today() - timedelta(days=5)
        result = self.Book.book_sale_save(self._sale_values(date=fields.Date.to_string(day)))
        invoice = self.Line.browse(result["ids"]).invoice_id
        self.assertFalse(invoice.invoice_payment_term_id)
        self.assertEqual(invoice.invoice_date_due, day, "not the day it was typed in")
        self.assertEqual(invoice.line_ids.filtered(lambda l: l.display_type == "payment_term").date_maturity, day)

    def test_terms_left_as_they_are_keep_the_customers(self):
        terms = self.env.ref("account.account_payment_term_15days")
        values = self._sale_values(partner_vat="")
        customer = self.env["res.partner"].browse(values["partner_id"])
        customer.write({"property_payment_term_id": terms.id, "vat": "P000591096X"})
        sale = self.Line.browse(self.Book.book_sale_save(values)["ids"]).stock_sale_id
        self.assertEqual(sale.payment_term_id, terms)
        self.assertEqual(sale.invoice_id.invoice_payment_term_id, terms)
        self.assertEqual((customer.vat, customer.property_payment_term_id), ("P000591096X", terms),
                         "an empty PIN box takes nothing off the customer")

    def test_a_pin_typed_must_be_a_kra_pin_and_a_foreign_tax_id_on_file_stays(self):
        values = self._sale_values(partner_vat="12345")
        with self.assertRaisesRegex(UserError, "not a KRA PIN"):
            self.Book.book_sale_save(values)
        customer = self.env["res.partner"].browse(values["partner_id"])
        customer.vat = "157041913"  # a Rwandan customer's tax ID
        values["partner_vat"] = "157041913"
        self.assertTrue(self.Book.book_sale_save(values)["posted"])
        self.assertEqual(customer.vat, "157041913")

    def test_a_new_customer_is_created_with_its_pin_and_terms(self):
        terms = self.env.ref("account.account_payment_term_30days")
        values = self._sale_values(partner_vat="A004416331M", payment_term_id=terms.id)
        values.update(partner_id=False, partner_name="Kiambu Club Test")
        customer = self.Line.browse(self.Book.book_sale_save(values)["ids"]).stock_sale_id.partner_id
        self.assertEqual((customer.name, customer.vat, customer.property_payment_term_id),
                         ("Kiambu Club Test", "A004416331M", terms))

    def test_a_cu_number_is_not_used_twice(self):
        self.Book.book_sale_save(self._sale_values(cu_invoice_no="KRACU0100012345/77"))
        with self.assertRaisesRegex(UserError, "already on invoice INV-1"):
            self.Book.book_sale_save(self._sale_values(purchase_ref="INV-2", cu_invoice_no="kracu0100012345/77"))

    def test_a_draft_sale_opens_with_its_pin_terms_and_cu_number_and_posts_with_them(self):
        terms = self.env.ref("account.account_payment_term_30days")
        day = fields.Date.today() - timedelta(days=3)
        values = self._sale_values(date=fields.Date.to_string(day), partner_vat="P052112729P",
                                   payment_term_id=terms.id, cu_invoice_no="CU-9")
        result = self.Book.book_sale_save(values, post=False)
        self.assertFalse(result["posted"])
        data = self.Book.book_sale_load(result["ids"][0])
        self.assertEqual((data["partner_vat"], data["payment_term_id"], data["cu_invoice_no"]),
                         ("P052112729P", terms.id, "CU-9"))
        self.assertEqual(data["supplier"]["payment_term_id"], terms.id)
        lines = self.Line.browse(result["ids"])
        lines.action_post()
        invoice = lines.invoice_id
        self.assertEqual(invoice.invoice_date_due, day + timedelta(days=30))
        if "doc_cu_invoice_no" in invoice._fields:
            self.assertEqual(invoice.doc_cu_invoice_no, "CU-9")

    # ------------------------------------------------------------ moves outside the book
    def _lines_for(self, product):
        return self.Line.search([("product_id", "=", product.id), ("state", "=", "posted")])

    def test_adjustment_outside_is_logged(self):
        before = self._lines_for(self.bottle)
        quant = self.env["stock.quant"].search([("product_id", "=", self.bottle.id), ("location_id", "=", self.stock.id)])
        quant.with_context(inventory_name="Direct count").write({"inventory_quantity": quant.quantity + 7})
        quant.with_context(inventory_name="Direct count").action_apply_inventory()
        new = self._lines_for(self.bottle) - before
        self.assertEqual(new.reason, "correction")
        self.assertEqual(new.qty_in, 7)
        self.assertTrue(new.ref)

    def test_scrap_outside_is_logged(self):
        before = self._lines_for(self.bottle)
        self.env["stock.scrap"].create({"product_id": self.bottle.id, "scrap_qty": 2, "location_id": self.stock.id,
                                        "product_uom_id": self.bottle.uom_id.id}).do_scrap()
        new = self._lines_for(self.bottle) - before
        self.assertEqual(new.reason, "spoiled")
        self.assertEqual(new.qty_out, 2)

    def test_receipt_without_an_order_is_logged_when_validated_not_before(self):
        supplier_loc = self.env.ref("stock.stock_location_suppliers")
        picking = self.env["stock.picking"].create({
            "picking_type_id": self.wh.in_type_id.id, "location_id": supplier_loc.id, "location_dest_id": self.stock.id,
            "partner_id": self.supplier.id, "move_ids": [Command.create({
                "product_id": self.bottle.id, "product_uom_qty": 5, "product_uom": self.bottle.uom_id.id,
                "location_id": supplier_loc.id, "location_dest_id": self.stock.id})]})
        before = self._lines_for(self.bottle)
        picking.action_confirm()
        self.assertFalse(self._lines_for(self.bottle) - before, "a draft moves nothing")
        self.Line.browse()._validate_picking(picking)
        new = self._lines_for(self.bottle) - before
        self.assertEqual(new.reason, "received")
        self.assertEqual(new.qty_in, 5)
        self.assertEqual(new.note, picking.name)

    def test_move_inside_the_warehouse_is_not_logged(self):
        shelf = self.env["stock.location"].create({"name": "Shelf", "location_id": self.stock.id, "usage": "internal"})
        before = self._lines_for(self.bottle)
        move = self.env["stock.move"].create({
            "product_id": self.bottle.id, "product_uom_qty": 1, "product_uom": self.bottle.uom_id.id,
            "location_id": self.stock.id, "location_dest_id": shelf.id, "company_id": self.env.company.id})
        move._action_confirm()
        move._set_quantity_done(1)
        move.picked = True
        move._action_done()
        self.assertFalse(self._lines_for(self.bottle) - before)

    def test_book_posting_is_not_logged_twice(self):
        line = self.Line.create({"product_id": self.bottle.id, "reason": "spoiled", "qty_out": 1})
        before = self.Line.search_count([])
        line.action_post()
        self.assertEqual(self.Line.search_count([]), before)

    def test_a_missing_line_cannot_stop_the_stock_move(self):
        before = self.onhand(self.bottle)
        with patch.object(type(self.env["stock.move"]), "_make_stock_book_line", side_effect=ValueError("broken")), \
                mute_logger("odoo.addons.odin_stock_book.models.purchase"):
            self.env["stock.scrap"].create({"product_id": self.bottle.id, "scrap_qty": 1, "location_id": self.stock.id,
                                            "product_uom_id": self.bottle.uom_id.id}).do_scrap()
        self.assertEqual(self.onhand(self.bottle), before - 1)
        self.assertGreaterEqual(self.Book.book_log_missing()["moves"], 1, "and it is found later")

    # ------------------------------------------------------------ undo
    def _saved_purchase(self, paid=True):
        result = self.Book.book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": "LPO-UNDO", "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(fields.Date.today()),
            "pay_journal_id": self.pay_journal.id if paid else False,
            "lines": [{"product_id": self.bottle.id, "qty": 10, "cost": 100.0, "tax_id": self.vat_net.id}],
        })
        return self.Line.browse(result["ids"])

    def test_undo_takes_everything_back_and_keeps_the_history(self):
        before = self.onhand(self.bottle)
        line = self._saved_purchase()
        window = line.stock_purchase_id
        order, bill, payment = window.order_id, window.bill_id, window.payment_id
        self.assertEqual(self.onhand(self.bottle), before + 10)
        result = self.Book.book_purchase_undo(line.id)
        self.assertEqual(self.onhand(self.bottle), before, "the goods are out again")
        self.assertEqual(payment.state, "canceled")
        self.assertEqual(bill.state, "cancel")
        self.assertEqual(order.state, "cancel")
        self.assertEqual(order.order_line.qty_received, 0)
        self.assertTrue(window.undone_on)
        self.assertEqual(result["again"]["purchase_ref"], "LPO-UNDO")
        back = self.Line.search([("purchase_line_id", "=", order.order_line.id), ("id", "!=", line.id)])
        self.assertEqual(back.qty_out, 10, "the return is on the log")
        self.assertFalse(self.Book.book_purchase_check(self.supplier.id, "LPO-UNDO"), "its number can be used again")
        row = self.Book._row(line, line._status(), 0, {})
        self.assertTrue(row["undone"])
        self.assertFalse(row["can_undo"])

    def test_undo_works_unpaid_and_can_be_entered_again(self):
        line = self._saved_purchase(paid=False)
        again = self.Book.book_purchase_undo(line.id)["again"]
        result = self.Book.book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": again["purchase_ref"], "warehouse_id": self.wh.id,
            "date": again["date"], "pay_journal_id": False,
            "lines": [{"product_id": self.bottle.id, "qty": 9, "cost": 100.0, "tax_id": self.vat_net.id}]})
        self.assertTrue(result["posted"])

    def test_undo_refused_once_the_goods_are_used(self):
        product = self.env["product.product"].create({
            "name": "Used Up", "is_storable": True, "type": "consu", "categ_id": self.bottle.categ_id.id,
            "supplier_taxes_id": False})
        result = self.Book.book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": "LPO-USED", "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(fields.Date.today()), "pay_journal_id": False,
            "lines": [{"product_id": product.id, "qty": 10, "cost": 100.0, "tax_id": self.vat_net.id}]})
        self.Line.create({"product_id": product.id, "reason": "spoiled", "qty_out": 4}).action_post()
        line = self.Line.browse(result["ids"])
        with self.assertRaises(UserError):
            self.Book.book_purchase_undo(line.id)
        self.assertFalse(line.stock_purchase_id.undone_on)
        self.assertEqual(line.stock_purchase_id.bill_id.state, "posted", "nothing was changed")

    # ------------------------------------------------------------ Undo on any posted line
    def _row(self, line):
        return self.Book._row(line, line._status(), line.onhand_after, {})

    def test_undo_a_sale_takes_it_back_and_leaves_a_draft_to_correct(self):
        before = self.onhand(self.fg)
        sale = self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 5, "partner_name": "Quickmart",
                                 "date": fields.Date.subtract(fields.Date.today(), days=3)})
        sale.action_post()
        self.assertTrue(self._row(sale)["can_undo"])
        self.assertEqual(self._row(sale)["undo_kind"], "reverse")
        self.assertFalse(self._row(sale)["can_reverse"], "Undo does it in one step")
        result = self.Book.book_undo(sale.id)
        self.assertEqual(self.onhand(self.fg), before, "the goods are back")
        back = self.Line.search([("reverses_id", "=", sale.id)])
        self.assertEqual((back.state, back.reason, back.qty_in), ("posted", "return", 5))
        self.assertEqual(back.note, f"Undoes {sale.ref}")
        self.assertTrue(sale.undone_on)
        self.assertEqual(sale.state, "posted", "it stays on the book")
        self.assertTrue(self._row(sale)["undone"])
        self.assertFalse(self._row(sale)["can_undo"])
        draft = self.Line.browse(result["draft_id"])
        self.assertEqual((draft.state, draft.date, draft.product_id, draft.qty_out, draft.partner_name),
                         ("draft", sale.date, self.fg, 5, "QUICKMART"), "the same line on its day, to correct")
        with self.assertRaises(UserError):
            self.Book.book_undo(sale.id)
        with self.assertRaises(UserError):
            self.Book.book_undo(back.id)  # the line that took it back is not undone itself

    def test_undo_refused_when_what_came_in_has_been_used(self):
        added = self.Line.create({"product_id": self.sticker.id, "reason": "correction", "qty_in": 5})
        added.action_post()
        self.Line.create({"product_id": self.sticker.id, "reason": "used", "qty_out": 403}).action_post()
        with self.assertRaisesRegex(UserError, "has been used"):
            self.Book.book_undo(added.id)
        self.assertFalse(added.undone_on)
        self.assertFalse(self.Line.search([("reverses_id", "=", added.id)]), "nothing was changed")
        self.assertEqual(self.onhand(self.sticker), 2)

    def test_undo_a_filling_unbuilds_it_and_keeps_what_was_used(self):
        before = {p: self.onhand(p) for p in (self.fg, self.bulk, self.bottle, self.carton, self.sticker)}
        line = self.Line.create({"product_id": self.fg.id, "reason": "filling", "qty_in": 24})
        line.component_ids.filtered(lambda c: c.product_id == self.bottle).qty = 25  # one broke
        line.action_post()
        self.assertEqual(self._row(line)["undo_kind"], "make")
        result = self.Book.book_undo(line.id)
        self.assertEqual({p: self.onhand(p) for p in before}, before, "made goods out, components back")
        unbuild = line.production_id.unbuild_ids
        self.assertEqual(unbuild.state, "done")
        logged = self.Line.search([("move_ids", "in", (unbuild.consume_line_ids | unbuild.produce_line_ids).ids)])
        self.assertTrue(logged, "the unbuild is on the log")
        self.assertEqual(set(logged.mapped("note")), {f"Undoes {line.ref}"})
        self.assertTrue(line.undone_on)
        draft = self.Line.browse(result["draft_id"])
        self.assertEqual(draft.reason, "filling")
        self.assertEqual({c.product_id: c.qty for c in draft.component_ids},
                         {c.product_id: c.qty for c in line.component_ids}, "the components as used, not the recipe")

    def test_undo_a_receipt_returns_it_and_cancels_its_order(self):
        before = self.onhand(self.bottle)
        line = self._received(qty=40, cost=12)
        line.action_post()
        order = line.purchase_id
        self.assertEqual(self._row(line)["undo_kind"], "received")
        result = self.Book.book_undo(line.id)
        self.assertEqual(self.onhand(self.bottle), before, "the goods went back")
        self.assertEqual(order.state, "cancel", "nothing else was on the order")
        self.assertEqual(order.order_line.qty_received, 0)
        back = self.Line.search([("purchase_line_id", "=", order.order_line.id), ("id", "!=", line.id)])
        self.assertEqual(back.qty_out, 40, "the return is on the log")
        draft = self.Line.browse(result["draft_id"])
        self.assertEqual((draft.reason, draft.qty_in, draft.unit_cost), ("received", 40, 12))

    def test_undo_one_receipt_of_an_order_keeps_the_other(self):
        first, second = self._received(qty=10, cost=12), self._received(product=self.carton, qty=5, cost=30)
        (first | second).action_post()
        order = first.purchase_id
        self.assertEqual(order, second.purchase_id, "one supplier, one day: one order")
        self.Book.book_undo(first.id)
        self.assertEqual(order.state, "purchase")
        self.assertEqual(first.purchase_line_id.product_qty, 0, "off the order")
        self.assertEqual(second.purchase_line_id.qty_received, 5, "the other stays received")

    def test_undo_refused_once_a_receipt_is_billed(self):
        line = self._received(qty=10, cost=12)
        line.action_post()
        line.purchase_id.action_create_invoice()
        self.assertIn("is billed", line._undo_refusal())
        self.assertFalse(self._row(line)["can_undo"])
        with self.assertRaises(UserError):
            self.Book.book_undo(line.id)

    def test_undo_a_sale_from_the_window_credits_it_and_opens_it_again(self):
        before = self.onhand(self.fg)
        values = self._sale_values()
        line = self.Line.browse(self.Book.book_sale_save(values)["ids"])
        window = line.stock_sale_id
        order, invoice = window.order_id, window.invoice_id
        self.assertEqual(self._row(line)["undo_kind"], "sale")
        result = self.Book.book_undo(line.id)
        self.assertEqual(result["kind"], "sale")
        self.assertEqual(self.onhand(self.fg), before, "the goods are back")
        self.assertEqual(order.state, "cancel")
        self.assertEqual(invoice.state, "posted", "a sales invoice is not cancelled")
        self.assertEqual(invoice.payment_state, "reversed")
        credit = window.credit_note_id
        self.assertEqual((credit.move_type, credit.state), ("out_refund", "posted"))
        self.assertEqual(credit.amount_total, invoice.amount_total)
        self.assertTrue(window.undone_on)
        self.assertTrue(self._row(line)["undone"])
        again = result["again"]
        self.assertTrue(again["keep_prices"])
        self.assertEqual(again["lines"][0]["cost"], 500.0)
        self.assertFalse(self.Book.book_sale_check(values["partner_id"], "INV-1"), "its number can be used again")
        self.assertTrue(self.Book.book_sale_save(values)["posted"])

    def test_undo_a_paid_sale_cancels_the_payment(self):
        self.pay_journal.inbound_payment_method_line_ids[:1].payment_account_id = self.outstanding
        line = self.Line.browse(self.Book.book_sale_save(self._sale_values(pay_journal_id=self.pay_journal.id))["ids"])
        payment = line.stock_sale_id.payment_id
        self.assertTrue(payment)
        self.Book.book_undo(line.id)
        self.assertEqual(payment.state, "canceled")
        self.assertEqual(line.invoice_id.payment_state, "reversed")

    def test_undo_routes_a_window_purchase_to_undo_purchase(self):
        line = self._saved_purchase(paid=False)
        result = self.Book.book_undo(line.id)
        self.assertEqual(result["kind"], "purchase")
        self.assertEqual(result["again"]["purchase_ref"], "LPO-UNDO")
        self.assertTrue(line.stock_purchase_id.undone_on)

    def test_a_delivery_made_in_sales_is_returned_there(self):
        customer = self.env["res.partner"].create({"name": "Carrefour Test"})
        order = self.env["sale.order"].create({"partner_id": customer.id, "order_line": [
            Command.create({"product_id": self.fg.id, "product_uom_qty": 3, "price_unit": 100})]})
        order.action_confirm()
        self.Line.browse()._validate_picking(order.picking_ids)
        logged = self.Line.search([("move_ids", "in", order.picking_ids.move_ids.ids)])
        self.assertEqual(logged.reason, "sale", "the delivery is on the log")
        self.assertIn("in Sales", logged._undo_refusal())
        self.assertFalse(self._row(logged)["can_undo"])

    def test_only_a_supervisor_undoes(self):
        storekeeper = new_test_user(self.env, "undo_storekeeper", groups="odin_stock_book.group_stock_book_user")
        line = self.Line.create({"product_id": self.fg.id, "reason": "spoiled", "qty_out": 1})
        line.action_post()
        self.assertFalse(self.Book.with_user(storekeeper)._row(line, line._status(), 0, {})["can_undo"])
        self.assertTrue(self.Book.with_user(storekeeper)._row(line, line._status(), 0, {})["can_reverse"],
                        "a storekeeper still has Reverse")
        with self.assertRaises(UserError):
            self.Book.with_user(storekeeper).book_undo(line.id)
