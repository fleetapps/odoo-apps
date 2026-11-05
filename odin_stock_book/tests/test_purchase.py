from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .test_stock_book import TestStockBook


@tagged("post_install", "-at_install")
class TestStockBookPurchase(TestStockBook):
    """Goods bought and in the store: one line, one post, and Purchases,
    Inventory and the book agree."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env["res.partner"].create({"name": "Safepak Limited"})
        (cls.bottle | cls.carton).supplier_taxes_id = False
        Tax = cls.env["account.tax"]
        cls.vat_net = Tax.create({"name": "Test 16 net", "amount": 16, "type_tax_use": "purchase",
                                  "price_include_override": "tax_excluded"})
        cls.vat_in = Tax.create({"name": "Test 16 in", "amount": 16, "type_tax_use": "purchase",
                                 "price_include_override": "tax_included"})
        # As on a live database: payments go through an outstanding account.
        # A new database's journals have none on their payment methods.
        cls.outstanding = cls.env["account.account"].create({
            "code": "120099", "name": "Test Outstanding Payments", "account_type": "asset_current", "reconcile": True})
        cls.pay_journal = cls.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", cls.env.company.id)], limit=1)
        cls.pay_journal.outbound_payment_method_line_ids[:1].payment_account_id = cls.outstanding

    def _received(self, product=None, qty=100, cost=14.5, **vals):
        return self.Line.create({"product_id": (product or self.bottle).id, "reason": "received", "qty_in": qty,
                                 "unit_cost": cost, "partner_name": self.supplier.name, **vals})

    def test_one_post_makes_the_order_receives_it_and_adds_the_stock(self):
        line = self._received()
        self.assertEqual(line._status()[0], "ready")
        before = self.onhand(self.bottle)
        line.action_post()
        order = line.purchase_id
        self.assertEqual(line.state, "posted")
        self.assertEqual(order.state, "purchase", "shows in Purchases as a confirmed order")
        self.assertEqual(order.partner_id, self.supplier)
        self.assertEqual(order.origin, line.ref)
        self.assertEqual(order.order_line.price_unit, 14.5)
        self.assertEqual(order.order_line.qty_received, 100, "Purchases knows it arrived")
        self.assertEqual(order.receipt_status, "full")
        self.assertEqual(order.invoice_status, "to invoice", "ready to bill, not billed")
        self.assertFalse(order.invoice_ids)
        self.assertEqual(line.picking_id, order.picking_ids)
        self.assertEqual(line.picking_id.state, "done")
        self.assertEqual(self.onhand(self.bottle), before + 100)
        self.assertEqual(line.onhand_after, before + 100)
        self.assertEqual(self.Line.search_count([("purchase_line_id", "=", order.order_line.id)]), 1,
                         "the book's own receipt is not logged twice")

    def test_cost_is_the_order_price(self):
        product = self.env["product.product"].create({
            "name": "New Label", "is_storable": True, "type": "consu", "categ_id": self.bottle.categ_id.id,
            "standard_price": 1.0, "supplier_taxes_id": False})
        line = self._received(product, qty=10, cost=20)
        line.action_post()
        self.assertEqual(sum(line.move_ids.mapped("value")), 200, "the receipt is worth what was paid")
        self.assertEqual(product.standard_price, 20, "the item's cost follows")

    def test_supplier_and_cost_are_needed(self):
        line = self.Line.create({"product_id": self.bottle.id, "reason": "received", "qty_in": 5})
        self.assertEqual(line._status()[0], "need_supplier")
        line.partner_name = self.supplier.name
        self.assertEqual(line._status()[0], "need_cost")
        with self.assertRaises(UserError):
            line.action_post()
        self.assertFalse(self.env["purchase.order"].search([("partner_id", "=", self.supplier.id)]))

    def test_posted_together_one_order_per_supplier_and_day(self):
        one = self._received(self.bottle, qty=10, cost=2)
        two = self._received(self.carton, qty=20, cost=3)
        other_day = self._received(self.bottle, qty=30, cost=4,
                                   date=fields.Date.context_today(self.Line) - timedelta(days=3))
        other = self._received(self.bottle, qty=40, cost=5, partner_name="Prime Cartons")
        (one | two | other_day | other).action_post()
        self.assertEqual(one.purchase_id, two.purchase_id)
        self.assertEqual(len(one.purchase_id.order_line), 2)
        self.assertEqual(one.picking_id, two.picking_id)
        self.assertNotEqual(one.ref, two.ref)
        self.assertIn(one.ref, one.purchase_id.origin)
        self.assertIn(two.ref, one.purchase_id.origin)
        self.assertEqual(one.move_ids.product_id, self.bottle, "each line keeps its own move")
        self.assertEqual(two.move_ids.product_id, self.carton)
        self.assertNotEqual(other_day.purchase_id, one.purchase_id)
        self.assertNotEqual(other.purchase_id, one.purchase_id)

    def test_same_item_twice_on_one_order(self):
        before = self.onhand(self.bottle)
        one = self._received(qty=10, cost=2)
        two = self._received(qty=5, cost=3)
        (one | two).action_post()
        self.assertEqual(one.purchase_id, two.purchase_id)
        self.assertEqual(one.move_ids.quantity, 10)
        self.assertEqual(two.move_ids.quantity, 5)
        self.assertEqual(self.onhand(self.bottle), before + 15)

    def test_back_dated_line_lands_on_its_day(self):
        day = fields.Date.context_today(self.Line) - timedelta(days=10)
        line = self._received(date=day)
        line.action_post()
        self.assertEqual(fields.Date.context_today(self.Line, line.move_ids.date), day)
        self.assertEqual(fields.Date.context_today(self.Line, line.picking_id.date_done), day)
        self.assertEqual(fields.Date.context_today(self.Line, line.purchase_id.date_order), day)

    def test_supervisor_without_purchase_rights_posts(self):
        user = new_test_user(self.env, "book_only", groups="odin_stock_book.group_stock_book_supervisor")
        line = self._received().with_user(user)
        line.action_post()
        self.assertEqual(line.sudo().purchase_id.state, "purchase")
        self.assertEqual(line.sudo().purchase_id.user_id, user)
        self.assertEqual(line.action_open_document()["res_model"], "stock.picking")

    def test_storekeeper_cannot_post(self):
        user = new_test_user(self.env, "book_keeper", groups="odin_stock_book.group_stock_book_user")
        line = self._received().with_user(user)
        with self.assertRaises(UserError):
            line.action_post()
        self.assertFalse(line.sudo().purchase_line_id)

    def _order(self, qty=50, price=7):
        order = self.env["purchase.order"].create({
            "partner_id": self.supplier.id,
            "order_line": [Command.create({"product_id": self.bottle.id, "product_qty": qty, "price_unit": price})],
        })
        order.button_confirm()
        return order

    def _validate(self, picking, qty=None):
        for move in picking.move_ids:
            move._set_quantity_done(qty or move.product_uom_qty)
            move.picked = True
        picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()

    def test_receipt_validated_in_purchases_is_on_the_book(self):
        before = self.onhand(self.bottle)
        order = self._order()
        self.assertFalse(self.Line.search([("purchase_line_id", "in", order.order_line.ids)]),
                         "nothing on the book until it arrives")
        self._validate(order.picking_ids)
        line = self.Line.search([("purchase_line_id", "in", order.order_line.ids)])
        self.assertEqual(len(line), 1)
        self.assertEqual(line.state, "posted")
        self.assertEqual(line.reason, "received")
        self.assertEqual(line.qty_in, 50)
        self.assertEqual(line.unit_cost, 7)
        self.assertTrue(line.ref.startswith("IN/"))
        self.assertEqual(line.picking_id, order.picking_ids)
        self.assertEqual(line.partner_id, self.supplier)
        self.assertEqual(line.onhand_after, before + 50)
        self.assertEqual(self.onhand(self.bottle), before + 50, "logged, not added a second time")
        rows = [r for r in self.Book.get_book()["rows"] if r["id"] == line.id]
        self.assertEqual(rows[0]["purchase"], order.name)

    def test_part_delivery_logs_what_arrived(self):
        order = self._order(qty=50)
        self._validate(order.picking_ids, qty=20)
        order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")).action_cancel()
        lines = self.Line.search([("purchase_line_id", "in", order.order_line.ids)])
        self.assertEqual(lines.mapped("qty_in"), [20])

    def test_return_to_supplier_is_on_the_book(self):
        line = self._received(qty=10, cost=2)
        line.action_post()
        before = self.onhand(self.bottle)
        wizard = self.env["stock.return.picking"].with_context(
            active_id=line.picking_id.id, active_ids=line.picking_id.ids, active_model="stock.picking",
        ).create({"picking_id": line.picking_id.id})
        wizard.product_return_moves.quantity = 4
        back = self.env["stock.picking"].browse(wizard.action_create_returns()["res_id"])
        self._validate(back, qty=4)
        out = self.Line.search([("purchase_line_id", "=", line.purchase_line_id.id), ("id", "!=", line.id)])
        self.assertEqual((out.reason, out.qty_out, out.state), ("correction", 4, "posted"))
        self.assertEqual(self.onhand(self.bottle), before - 4)
        self.assertEqual(line.purchase_line_id.qty_received, 6)

    # ------------------------------------------------------------ VAT on inline lines
    def test_inline_cost_is_before_vat_even_if_the_item_defaults_to_included(self):
        """The default tax of an item can be VAT-inclusive; the inline cost is not."""
        self.bottle.supplier_taxes_id = self.vat_in
        line = self._received(qty=10, cost=100)
        line.action_post()
        order_line = line.purchase_line_id
        self.assertTrue(order_line.tax_ids)
        self.assertFalse(order_line.tax_ids.price_include)
        self.assertEqual(order_line.tax_ids.amount, 16)
        self.assertEqual(order_line.price_subtotal, 1000)
        self.assertEqual(order_line.price_total, 1160)
        self.assertEqual(sum(line.move_ids.mapped("value")), 1000, "stock at the cost typed, VAT on top")

    def test_inline_takes_the_vat_last_used_for_the_item(self):
        self.bottle.supplier_taxes_id = self.vat_in
        order = self.env["purchase.order"].create({
            "partner_id": self.supplier.id,
            "order_line": [Command.create({"product_id": self.bottle.id, "product_qty": 1, "price_unit": 5,
                                           "tax_ids": [Command.set(self.vat_net.ids)]})],
        })
        order.button_confirm()
        line = self._received(qty=1, cost=7)
        line.action_post()
        self.assertEqual(line.purchase_line_id.tax_ids, self.vat_net)

    # ------------------------------------------------------------ the purchase window
    def _window(self, ref="LPO-100", lines=None, post=True, **vals):
        rows = lines or [
            {"product_id": self.bottle.id, "qty": 100, "cost": 10, "tax_id": self.vat_net.id},
            {"product_id": self.carton.id, "qty": 5, "cost": 200, "tax_id": self.vat_net.id},
        ]
        return self.Book.book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": ref, "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(fields.Date.context_today(self.Line)), "lines": rows,
            "pay_journal_id": False, **vals,
        }, post=post)

    def test_window_makes_one_order_with_the_number_and_a_line_per_item(self):
        bottles, cartons = self.onhand(self.bottle), self.onhand(self.carton)
        result = self._window()
        order = self.env["purchase.order"].browse(result["order_id"])
        lines = self.Line.browse(result["ids"])
        self.assertTrue(result["posted"])
        self.assertEqual(order.partner_ref, "LPO-100")
        self.assertEqual(order.partner_id, self.supplier)
        self.assertEqual(order.state, "purchase")
        self.assertEqual(order.receipt_status, "full")
        self.assertEqual(order.amount_untaxed, 2000)
        self.assertEqual(order.amount_tax, 320)
        self.assertEqual(order.amount_total, 2320)
        self.assertEqual(result["total"], 2320)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines.purchase_id, order)
        self.assertEqual(set(lines.mapped("state")), {"posted"})
        self.assertEqual(set(lines.mapped("purchase_ref")), {"LPO-100"})
        self.assertEqual(lines.partner_id, self.supplier)
        self.assertEqual(self.onhand(self.bottle), bottles + 100)
        self.assertEqual(self.onhand(self.carton), cartons + 5)
        self.assertEqual(sum(lines.move_ids.mapped("value")), 2000, "stock at the cost without VAT")
        rows = {r["id"]: r for r in self.Book.get_book()["rows"]}
        self.assertEqual(rows[lines[0].id]["purchase_ref"], "LPO-100")
        self.assertEqual(rows[lines[0].id]["purchase"], order.name)
        self.assertIn("LPO-100", rows[lines[0].id]["note_print"])

    def test_totals_preview_equals_the_order(self):
        rows = [{"product_id": self.bottle.id, "qty": 3, "cost": 33.33, "tax_id": self.vat_net.id},
                {"product_id": self.carton.id, "qty": 7, "cost": 11.11, "tax_id": self.vat_in.id}]
        preview = self.Book.book_purchase_totals(rows, self.supplier.id)
        result = self._window(lines=rows)
        order = self.env["purchase.order"].browse(result["order_id"])
        self.assertAlmostEqual(preview["subtotal"], order.amount_untaxed)
        self.assertAlmostEqual(preview["vat"], order.amount_tax)
        self.assertAlmostEqual(preview["total"], order.amount_total)
        self.assertEqual(preview["lines"], order.order_line.mapped("price_subtotal"))

    def test_included_vat_values_the_stock_without_it(self):
        result = self._window(lines=[{"product_id": self.bottle.id, "qty": 10, "cost": 116, "tax_id": self.vat_in.id}])
        order = self.env["purchase.order"].browse(result["order_id"])
        self.assertEqual(order.amount_total, 1160)
        self.assertEqual(order.amount_untaxed, 1000)
        self.assertEqual(sum(self.Line.browse(result["ids"]).move_ids.mapped("value")), 1000)

    def test_a_number_goes_in_once_per_supplier(self):
        self._window(ref="LPO-200")
        count = self.Line.search_count([])
        for ref in ("LPO-200", "lpo-200", " LPO-200 "):
            with self.assertRaises(UserError):
                self._window(ref=ref)
        self.assertEqual(self.Line.search_count([]), count)
        self.assertTrue(self.Book.book_purchase_check(self.supplier.id, "lpo-200"))
        self.assertFalse(self.Book.book_purchase_check(self.supplier.id, "LPO-201"))
        other = self.env["res.partner"].create({"name": "Other Supplier"})
        self.assertTrue(self._window(ref="LPO-200", partner_id=other.id)["order"], "another supplier's number is theirs")

    def test_nothing_is_saved_if_a_line_is_wrong(self):
        count = self.Line.search_count([])
        good = {"product_id": self.bottle.id, "qty": 1, "cost": 1, "tax_id": self.vat_net.id}
        for bad in ({"qty": 0}, {"cost": 0}, {"tax_id": False}, {"product_id": False}):
            with self.assertRaises(UserError):
                self._window(ref="LPO-300", lines=[good, {**good, **bad}])
        future = fields.Date.to_string(fields.Date.context_today(self.Line) + timedelta(days=1))
        with self.assertRaises(UserError):
            self._window(ref="LPO-300", date=future)
        with self.assertRaises(UserError):
            self._window(ref="   ")
        self.assertEqual(self.Line.search_count([]), count)

    def test_new_supplier_by_name(self):
        result = self._window(ref="LPO-400", partner_id=False, partner_name="Brand New Ltd")
        order = self.env["purchase.order"].browse(result["order_id"])
        self.assertEqual(order.partner_id.name, "Brand New Ltd")
        self.assertGreater(order.partner_id.supplier_rank, 0)

    def test_storekeeper_saves_and_a_supervisor_posts_the_whole_lpo(self):
        keeper = new_test_user(self.env, "lpo_keeper", groups="odin_stock_book.group_stock_book_user")
        result = self.Book.with_user(keeper).book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": "LPO-500", "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(fields.Date.context_today(self.Line)),
            "lines": [{"product_id": self.bottle.id, "qty": 2, "cost": 3, "tax_id": self.vat_net.id},
                      {"product_id": self.carton.id, "qty": 4, "cost": 5, "tax_id": self.vat_net.id}],
        })
        lines = self.Line.browse(result["ids"])
        self.assertFalse(result["posted"])
        self.assertEqual(set(lines.mapped("state")), {"draft"})
        self.assertFalse(lines.purchase_id)
        with self.assertRaises(UserError):
            self._window(ref="LPO-500")
        posted = self.Book.book_post(lines[0].ids)
        self.assertEqual(posted["count"], 2, "the whole LPO, though one line was posted")
        self.assertEqual(len(lines.purchase_id), 1)
        self.assertEqual(lines.purchase_id.partner_ref, "LPO-500")

    def test_supervisor_without_purchase_rights_uses_the_window(self):
        user = new_test_user(self.env, "lpo_super", groups="odin_stock_book.group_stock_book_supervisor")
        Book = self.Book.with_user(user)
        self.assertTrue(Book.book_purchase_options()["can_post"])
        self.assertTrue(Book.book_purchase_suppliers("Safepak")["items"])
        self.assertEqual(Book.book_purchase_item(self.bottle.id, self.supplier.id)["tax_id"], False)
        self.assertEqual(Book.book_purchase_totals(
            [{"product_id": self.bottle.id, "qty": 1, "cost": 100, "tax_id": self.vat_net.id}])["total"], 116)
        result = Book.book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": "LPO-600", "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(fields.Date.context_today(self.Line)),
            "lines": [{"product_id": self.bottle.id, "qty": 1, "cost": 100, "tax_id": self.vat_net.id}],
        })
        self.assertEqual(result["total"], 116)
        self.assertTrue(Book.get_book()["rows"])

    def test_receipt_from_purchases_carries_its_number(self):
        order = self._order()
        order.partner_ref = "LPO-700"
        self._validate(order.picking_ids)
        line = self.Line.search([("purchase_line_id", "in", order.order_line.ids)])
        self.assertEqual(line.purchase_ref, "LPO-700")

    def test_window_starts_on_vat_added_on_top(self):
        """A new item from a new supplier whose default is included in the price."""
        self.bottle.supplier_taxes_id = self.vat_in
        new = self.env["res.partner"].create({"name": "First Time Supplier"})
        tax = self.env["account.tax"].browse(self.Book.book_purchase_item(self.bottle.id, new.id)["tax_id"])
        self.assertEqual(tax.amount, 16)
        self.assertFalse(tax.price_include)

    def test_an_lpos_lines_cannot_drift_apart(self):
        keeper = new_test_user(self.env, "lpo_dates", groups="odin_stock_book.group_stock_book_user")
        today = fields.Date.context_today(self.Line)
        result = self.Book.with_user(keeper).book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": "LPO-800", "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(today),
            "lines": [{"product_id": self.bottle.id, "qty": 2, "cost": 3, "tax_id": self.vat_net.id},
                      {"product_id": self.carton.id, "qty": 4, "cost": 5, "tax_id": self.vat_net.id}],
        })
        lines = self.Line.browse(result["ids"])
        with self.assertRaises(UserError):
            lines[1].date = today - timedelta(days=2)
        self.Book.book_post(lines[0].ids)
        self.assertEqual(set(lines.mapped("state")), {"posted"})
        self.assertEqual(len(lines.purchase_id), 1)

    # ------------------------------------------------------------ bill and payment
    def _service(self):
        return self.env["product.product"].create({"name": "Transport", "type": "service", "purchase_ok": True,
                                                   "supplier_taxes_id": False})

    def test_window_posts_the_bill_on_the_day_and_number(self):
        day = fields.Date.context_today(self.Line) - timedelta(days=5)
        result = self._window(ref="LPO-900", date=fields.Date.to_string(day))
        order = self.env["purchase.order"].browse(result["order_id"])
        bill = order.invoice_ids
        self.assertEqual(len(bill), 1)
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.journal_id.type, "purchase")
        self.assertEqual(bill.ref, "LPO-900")
        self.assertEqual(bill.invoice_date, day)
        self.assertEqual(bill.date, day)
        self.assertEqual(bill.partner_id, self.supplier)
        self.assertEqual(bill.amount_total, order.amount_total)
        self.assertEqual(bill.payment_state, "not_paid")
        self.assertEqual(order.invoice_status, "invoiced")
        self.assertEqual(result["bill"], bill.name)

    def test_window_pays_from_the_journal_chosen(self):
        journals = self.Book._purchase_pay_journals()
        self.assertIn(self.pay_journal, journals)
        for journal in journals:
            result = self._window(ref=f"LPO-91-{journal.id}", pay_journal_id=journal.id)
            bill = self.env["purchase.order"].browse(result["order_id"]).invoice_ids
            payment = self.env["odin.stock.purchase"].search([("bill_id", "=", bill.id)]).payment_id
            self.assertEqual(payment.journal_id, journal)
            self.assertEqual(payment.amount, bill.amount_total)
            self.assertEqual(payment.date, bill.invoice_date)
            self.assertTrue(payment.move_id, "the payment is in the ledger")
            self.assertEqual(payment.outstanding_account_id, self.outstanding)
            self.assertIn(bill.payment_state, ("in_payment", "paid"))

    def test_a_journal_with_no_ledger_entry_cannot_pay(self):
        journal = self.env["account.journal"].create({"name": "Float", "code": "FLT9", "type": "cash"})
        journal.outbound_payment_method_line_ids.payment_account_id = False
        self.assertNotIn(journal, self.Book._purchase_pay_journals())
        with self.assertRaises(UserError):
            self._window(ref="LPO-915", pay_journal_id=journal.id)

    def test_other_costs_go_on_the_bill_not_in_stock(self):
        transport = self._service()
        bottles = self.onhand(self.bottle)
        result = self._window(ref="LPO-920", lines=[
            {"product_id": self.bottle.id, "qty": 10, "cost": 10, "tax_id": self.vat_net.id},
            {"product_id": transport.id, "qty": 1, "cost": 500, "tax_id": self.vat_net.id, "note": "From Mombasa"},
        ])
        order = self.env["purchase.order"].browse(result["order_id"])
        lines = self.Line.browse(result["ids"])
        self.assertEqual(len(lines), 1, "only stock is a line on the book")
        self.assertEqual(lines.product_id, self.bottle)
        self.assertEqual(self.onhand(self.bottle), bottles + 10)
        self.assertEqual(sum(lines.move_ids.mapped("value")), 100, "transport is not in the stock value")
        self.assertEqual(order.amount_untaxed, 600)
        bill_line = order.invoice_ids.invoice_line_ids.filtered(lambda l: l.product_id == transport)
        self.assertEqual(bill_line.price_subtotal, 500)
        self.assertIn("From Mombasa", bill_line.name)
        self.assertEqual(order.invoice_status, "invoiced")

    def test_only_other_costs_is_refused(self):
        count = self.env["odin.stock.purchase"].search_count([])
        with self.assertRaises(UserError):
            self._window(ref="LPO-930", lines=[
                {"product_id": self._service().id, "qty": 1, "cost": 500, "tax_id": self.vat_net.id}])
        self.assertEqual(self.env["odin.stock.purchase"].search_count([]), count)

    def test_nothing_stays_if_the_payment_fails(self):
        journal = self.pay_journal
        bottles = self.onhand(self.bottle)
        Purchase = type(self.env["odin.stock.purchase"])
        with patch.object(Purchase, "_pay", side_effect=UserError("No")):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self._window(ref="LPO-940", pay_journal_id=journal.id)
        self.assertEqual(self.onhand(self.bottle), bottles)
        self.assertFalse(self.env["purchase.order"].search([("partner_ref", "=", "LPO-940")]))
        self.assertFalse(self.env["account.move"].search([("ref", "=", "LPO-940")]))

    def test_a_billed_number_is_refused(self):
        self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.supplier.id, "ref": "LPO-950",
            "invoice_date": fields.Date.context_today(self.Line),
            "invoice_line_ids": [Command.create({"name": "x", "quantity": 1, "price_unit": 1, "tax_ids": False})],
        })
        with self.assertRaises(UserError):
            self._window(ref="lpo-950")

    def test_storekeeper_entry_is_billed_when_a_supervisor_posts(self):
        keeper = new_test_user(self.env, "lpo_bill", groups="odin_stock_book.group_stock_book_user")
        transport = self._service()
        result = self.Book.with_user(keeper).book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": "LPO-960", "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(fields.Date.context_today(self.Line)),
            "lines": [{"product_id": self.bottle.id, "qty": 2, "cost": 3, "tax_id": self.vat_net.id},
                      {"product_id": transport.id, "qty": 1, "cost": 50, "tax_id": self.vat_net.id}],
        })
        lines = self.Line.browse(result["ids"])
        self.assertFalse(self.env["account.move"].search([("ref", "=", "LPO-960")]))
        with self.assertRaises(UserError):
            lines.partner_name = "Someone Else"
        self.Book.book_post(lines.ids)
        bill = lines.purchase_id.invoice_ids
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.amount_untaxed, 56)

    def test_received_without_a_bill_shows_to_bill(self):
        before = self.Book.get_book()["to_bill"]
        self._received().action_post()
        self.assertEqual(self.Book.get_book()["to_bill"], before + 1)

    # ------------------------------------------------------------ the cards and the book
    def test_needs_card_shows_what_it_counts(self):
        self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 1})  # no customer: needs
        self.Line.create({"product_id": self.fg.id, "reason": "sale", "qty_out": 99999, "partner_name": "A"})  # short
        book = self.Book.get_book(status="need")
        self.assertEqual(len(book["rows"]), book["counts"]["need"])
        self.assertEqual({r["status_key"] for r in book["rows"]}, {"need_partner"})
        self.assertEqual({r["status_key"] for r in self.Book.get_book(status="short")["rows"]}, {"short"})

    def test_out_for_recipes_lists_what_stops_a_batch(self):
        self.env["stock.quant"]._update_available_quantity(self.carton, self.stock, -self.onhand(self.carton))
        data = self.Book.get_recipes()
        self.assertEqual(self.Book.get_book()["out_in_recipes"]["count"], data["items"])
        row = next(r for r in data["rows"] if r["product"]["id"] == self.carton.id)
        self.assertEqual(row["recipe_count"], 1)
        self.assertIn(self.fg.name, row["recipes"])
        self.assertFalse(row["made"])
        self.assertEqual(row["reason"], "received")
        self.assertTrue(row["product"]["stock"])
        self._window(ref="LPO-R1", lines=[{"product_id": self.carton.id, "qty": 5, "cost": 2, "tax_id": self.vat_net.id}])
        self.assertFalse(self.Book.get_recipes(search=self.carton.default_code)["rows"], "bought: no longer out")

    def test_archived_item_stops_no_batch(self):
        self.env["stock.quant"]._update_available_quantity(self.carton, self.stock, -self.onhand(self.carton))
        self.assertIn(self.carton.id, [r["product"]["id"] for r in self.Book.get_recipes()["rows"]])
        self.carton.active = False
        self.assertNotIn(self.carton.id, [r["product"]["id"] for r in self.Book.get_recipes()["rows"]])

    def test_a_needs_cost_line_becomes_a_purchase(self):
        line = self.Line.create({"product_id": self.bottle.id, "reason": "received", "qty_in": 7,
                                 "partner_name": self.supplier.name})
        self.assertEqual(line._status()[0], "need_cost")
        loaded = self.Book.book_purchase_load(line.id)
        self.assertEqual(loaded["supplier"]["id"], self.supplier.id)
        self.assertEqual(loaded["replace_ids"], line.ids)
        self.assertEqual(loaded["lines"][0]["qty"], 7)
        self.assertFalse(loaded["edit"])
        result = self.Book.book_purchase_save({
            "partner_id": self.supplier.id, "purchase_ref": "LPO-C1", "warehouse_id": self.wh.id,
            "date": loaded["date"], "pay_journal_id": False,
            "lines": [{"product_id": self.bottle.id, "qty": 7, "cost": 3, "tax_id": self.vat_net.id}],
        }, replace_ids=loaded["replace_ids"])
        self.assertFalse(line.exists(), "replaced, not left beside it to be posted twice")
        self.assertEqual(self.Line.browse(result["ids"]).qty_in, 7)

    def test_a_draft_lpo_can_be_edited(self):
        keeper = new_test_user(self.env, "lpo_edit", groups="odin_stock_book.group_stock_book_user")
        transport = self._service()
        values = {
            "partner_id": self.supplier.id, "purchase_ref": "LPO-E1", "warehouse_id": self.wh.id,
            "date": fields.Date.to_string(fields.Date.context_today(self.Line)), "pay_journal_id": self.pay_journal.id,
            "lines": [{"product_id": self.bottle.id, "qty": 2, "cost": 3, "tax_id": self.vat_net.id},
                      {"product_id": transport.id, "qty": 1, "cost": 50, "tax_id": self.vat_net.id, "note": "Boda"}],
        }
        Book = self.Book.with_user(keeper)
        first = Book.book_purchase_save(values)
        loaded = Book.book_purchase_load(first["ids"][0])
        self.assertTrue(loaded["edit"])
        self.assertEqual(loaded["paid"], str(self.pay_journal.id))
        self.assertEqual([r["product"]["stock"] for r in loaded["lines"]], [True, False])
        self.assertEqual(loaded["lines"][1]["note"], "Boda")
        self.assertFalse(Book.book_purchase_check(self.supplier.id, "LPO-E1", loaded["replace_ids"]))
        self.assertTrue(Book.book_purchase_check(self.supplier.id, "LPO-E1"))
        values["lines"][0]["qty"] = 9
        second = Book.book_purchase_save(values, replace_ids=loaded["replace_ids"])
        self.assertFalse(self.Line.browse(first["ids"]).exists())
        self.assertEqual(self.Line.browse(second["ids"]).qty_in, 9)
        self.assertEqual(self.env["odin.stock.purchase"].search_count([("purchase_ref", "=", "LPO-E1")]), 1)

    # ------------------------------------------------------------ bulk posting
    def test_bulk_post_goes_in_date_order_as_the_page_projects(self):
        """A delivery lets the use after it through: it used to be left out silently."""
        have = self.onhand(self.sticker)
        day = fields.Date.context_today(self.Line)
        receipt = self._received(self.sticker, qty=100, cost=2, date=day - timedelta(days=2))
        use = self.Line.create({"product_id": self.sticker.id, "reason": "used", "qty_out": have + 50,
                                "date": day - timedelta(days=1)})
        rows = {r["id"]: r for r in self.Book.get_book(day.year, day.month)["rows"]} if use.date.month == day.month else {}
        if rows:
            self.assertEqual(rows[use.id]["status_key"], "ready", "the page projects the delivery first")
        result = self.Book.book_post((use | receipt).ids)
        self.assertEqual(result["count"], 2)
        self.assertEqual((receipt.state, use.state), ("posted", "posted"))
        self.assertEqual(self.onhand(self.sticker), 50)
        self.assertFalse(result["skipped"] or result["failed"])

    def test_bulk_post_reports_what_stayed_and_posts_the_rest(self):
        good = self.Line.create({"product_id": self.fg.id, "reason": "used", "qty_out": 1})
        short = self.Line.create({"product_id": self.sticker.id, "reason": "used", "qty_out": 999999})
        result = self.Book.book_post((good | short).ids)
        self.assertEqual(result["count"], 1)
        self.assertEqual(good.state, "posted")
        self.assertEqual(short.state, "draft")
        self.assertEqual([s["id"] for s in result["skipped"]], short.ids)

    def test_bulk_post_survives_a_line_that_fails(self):
        good = self.Line.create({"product_id": self.fg.id, "reason": "used", "qty_out": 1})
        bad = self.Line.create({"product_id": self.fg.id, "reason": "spoiled", "qty_out": 1})
        Line = type(self.Line)
        original = Line._post_scrap
        def refuse(line):
            if line.reason == "spoiled":
                raise UserError("Refused")
            return original(line)
        with patch.object(Line, "_post_spoiled", refuse):
            result = self.Book.book_post((good | bad).ids)
        self.assertEqual(good.state, "posted")
        self.assertEqual(bad.state, "draft")
        self.assertFalse(bad.ref, "its number goes back")
        self.assertEqual(result["failed"][0]["why"], "Refused")

    def test_bulk_post_keeps_a_supplier_day_on_one_order(self):
        one = self._received(self.bottle, qty=1, cost=2)
        two = self._received(self.carton, qty=1, cost=2)
        self.assertEqual(self.Book.book_post((one | two).ids)["count"], 2)
        self.assertEqual(one.purchase_id, two.purchase_id)

    def test_storekeeper_cannot_bulk_post(self):
        keeper = new_test_user(self.env, "bulk_keeper", groups="odin_stock_book.group_stock_book_user")
        line = self.Line.create({"product_id": self.fg.id, "reason": "used", "qty_out": 1})
        with self.assertRaises(UserError):
            self.Book.with_user(keeper).book_post(line.ids)
        self.assertEqual(line.state, "draft")
