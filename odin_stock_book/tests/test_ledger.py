from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

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
        with patch.object(type(self.env["stock.move"]), "_make_stock_book_line", side_effect=ValueError("broken")):
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
