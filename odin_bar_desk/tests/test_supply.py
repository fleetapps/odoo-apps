from odoo.exceptions import UserError
from odoo.tests import freeze_time, tagged

from .common import BarDeskCase


@tagged("post_install", "-at_install")
class TestAskForStock(BarDeskCase):
    """A bar asks the Main Store or another bar; they send what they have."""

    def setUp(self):
        super().setUp()
        self.opening_stock(self.loc_store, [(self.tusker, 100)])
        self.opening_stock(self.loc_bb, [(self.jameson, 50)])
        self.be_token = self.login(self.device_be, self.bar_be, self.mary, "1234")
        self.store_token = self.login(self.device_store, self.store, self.sam, "4321")
        self.bb_token = self.login(self.device_bb, self.bar_bb, self.john, "5678")

    def ask(self, source, lines):
        result = self.desk(self.device_be).desk_request_create(
            self.bar_be.id, self.be_token, self.uuid(), source.id, lines
        )
        return self.env["odin.bar.activity"].browse(result["activity_id"]).request_id

    def test_store_sends_part_and_a_bar_sends_the_rest(self):
        request = self.ask(
            self.store,
            [
                {"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 2},
                {"product_id": self.jameson.id, "uom_id": self.uom_750.id, "qty": 1},
            ],
        )
        self.assertEqual(request.state, "open")
        self.assertEqual(self.qty(self.tusker, self.loc_be), 0, "asking moves nothing")
        home = self.desk(self.device_store).desk_home(self.store.id, self.store_token)
        self.assertEqual(home["requests_in"], 1)
        incoming = self.desk(self.device_store).desk_requests(self.store.id, self.store_token)["incoming"]
        self.assertEqual([r["id"] for r in incoming], request.ids)

        # The store has the beer but no Jameson.
        tusker_line, jameson_line = request.line_ids
        self.desk(self.device_store).desk_request_answer(
            self.store.id, self.store_token, self.uuid(), request.id, {tusker_line.id: 2, jameson_line.id: 0}
        )
        self.assertEqual(request.state, "partial")
        self.assertEqual(request.answered_by_id, self.sam)
        self.assertEqual(request.picking_id.picking_type_id, self.bar_be.issue_type_id)
        self.assertEqual(request.picking_id.bar_ack_state, "not_checked", "it shows in Stock in")
        self.assertEqual(self.qty(self.tusker, self.loc_be), 48)
        self.assertEqual(jameson_line.missing_qty, 1)

        mine = self.desk(self.device_be).desk_requests(self.bar_be.id, self.be_token)["mine"]
        info = next(r for r in mine if r["id"] == request.id)
        self.assertEqual({s["id"] for s in info["ask_next"]}, set(self.bar_bb.ids))
        self.assertEqual(self.desk(self.device_be).desk_home(self.bar_be.id, self.be_token)["asks_missing"], 1)

        # Banda Bar has it: pass the missing line on.
        result = self.desk(self.device_be).desk_request_pass_on(
            self.bar_be.id, self.be_token, self.uuid(), request.id, self.bar_bb.id
        )
        passed = self.env["odin.bar.activity"].browse(result["activity_id"]).request_id
        self.assertEqual(passed.passed_from_id, request)
        self.assertEqual(passed.line_ids.mapped(lambda l: (l.product_id, l.qty)), [(self.jameson, 1)])
        self.assertEqual(self.desk(self.device_be).desk_home(self.bar_be.id, self.be_token)["asks_missing"], 0)
        self.desk(self.device_bb).desk_request_answer(
            self.bar_bb.id, self.bb_token, self.uuid(), passed.id, {passed.line_ids.id: 1}
        )
        self.assertEqual(passed.state, "sent")
        self.assertEqual(self.qty(self.jameson, self.loc_be), 25)
        self.assertEqual(self.qty(self.jameson, self.loc_bb), 25)

    def test_only_the_location_asked_answers(self):
        request = self.ask(self.store, [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6}])
        with self.assertRaisesRegex(UserError, "not made to Banda Bar"):
            self.desk(self.device_bb).desk_request_answer(
                self.bar_bb.id, self.bb_token, self.uuid(), request.id, {request.line_ids.id: 6}
            )
        with self.assertRaisesRegex(UserError, "Pick where to ask"):
            self.ask(self.bar_be, [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 1}])

    def test_nothing_available_and_cancel(self):
        request = self.ask(self.bar_bb, [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6}])
        self.desk(self.device_bb).desk_request_answer(
            self.bar_bb.id, self.bb_token, self.uuid(), request.id, {request.line_ids.id: 0}
        )
        self.assertEqual(request.state, "none")
        self.assertFalse(request.picking_id)
        other = self.ask(self.store, [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6}])
        self.desk(self.device_be).desk_request_cancel(self.bar_be.id, self.be_token, other.id)
        self.assertEqual(other.state, "cancel")
        with self.assertRaisesRegex(UserError, "already answered or cancelled"):
            self.desk(self.device_store).desk_request_answer(
                self.store.id, self.store_token, self.uuid(), other.id, {other.line_ids.id: 6}
            )

    def test_answer_is_posted_once(self):
        request = self.ask(self.store, [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6}])
        uuid = self.uuid()
        args = (self.store.id, self.store_token, uuid, request.id, {request.line_ids.id: 6})
        first = self.desk(self.device_store).desk_request_answer(*args)
        again = self.desk(self.device_store).desk_request_answer(*args)
        self.assertEqual(first, again)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 6)


@tagged("post_install", "-at_install")
class TestOrderAndPay(BarDeskCase):
    """Order from suppliers at 15:00, receive and pay the next day."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env["res.partner"].create(
            {"name": "Kenya Breweries", "email": "orders@kbl.example", "phone": "0712 345678"}
        )
        cls.env["product.supplierinfo"].create(
            {"partner_id": cls.supplier.id, "product_tmpl_id": cls.tusker.product_tmpl_id.id, "price": 100.0}
        )
        cls.tusker.write({"bar_order_uom_id": cls.uom_crate.id, "bar_usual_qty": 5})
        cls.sam.odin_bar_can_order = True
        cls.cash = cls.env["account.journal"].search(
            [("type", "in", ("cash", "bank")), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.store.supplier_payment_journal_id = cls.cash

    def setUp(self):
        super().setUp()
        self.token = self.login(self.device_store, self.store, self.sam, "4321")
        self.desk_store = self.desk(self.device_store)

    def suggestion(self):
        data = self.desk_store.desk_order_suggestions(self.store.id, self.token)
        return next((s for s in data["suppliers"] if s["id"] == self.supplier.id), None)

    def order(self, crates):
        result = self.desk_store.desk_order_send(
            self.store.id,
            self.token,
            self.uuid(),
            self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": crates}],
        )
        return result, self.env["odin.bar.activity"].browse(result["activity_id"]).purchase_id

    def test_suggestion_starts_from_this_mornings_count(self):
        with freeze_time("2026-09-30 05:00:00"):
            self.opening_stock(self.loc_store, [(self.tusker, 60)])
        with freeze_time("2026-09-30 07:00:00"):  # 10:00 stock take: 20 left, not approved yet
            self.submit_count(
                self.device_store, self.store, self.sam, "4321", [self.count_line(self.tusker, units=20)]
            )
        with freeze_time("2026-09-30 12:00:00"):  # 15:00
            line = self.suggestion()["lines"][0]
            # usual 5 crates = 120; 20 counted -> 100 more -> 5 crates of 24
            self.assertEqual((line["product_id"], line["uom_id"], line["qty"]), (self.tusker.id, self.uom_crate.id, 5))
            result, order = self.order(4)
        self.assertEqual(order.state, "purchase")
        self.assertEqual(order.picking_type_id, self.store.receipt_type_id)
        self.assertEqual(order.order_line.product_qty, 4)
        self.assertEqual(order.order_line.price_unit, 2400.0)
        self.assertTrue(result["order"]["emailed"])
        self.assertTrue(result["order"]["whatsapp"].startswith("https://wa.me/"))
        self.assertIn("4 × Crate of 24", result["order"]["text"])
        with freeze_time("2026-09-30 12:05:00"):
            again = self.suggestion()
        self.assertEqual(again["lines"][0]["qty"], 1, "what is on order counts: 100 - 96 -> 1 crate")
        self.assertEqual(again["ordered"][0]["name"], order.name)

    def test_only_staff_who_order_see_it(self):
        self.sam.odin_bar_can_order = False
        self.assertFalse(self.desk_store.desk_home(self.store.id, self.token)["can_order"])
        with self.assertRaisesRegex(UserError, "allowed to order"):
            self.desk_store.desk_order_suggestions(self.store.id, self.token)

    def receive(self, order, received_crates, paid, rest="coming", ref=False):
        receipt = order.picking_ids
        detail = self.desk_store.desk_receipt(self.store.id, self.token, receipt.id)
        move_id = detail["lines"][0]["move_id"]
        self.desk_store.desk_receipt_validate(
            self.store.id, self.token, self.uuid(), receipt.id, {move_id: received_crates},
            paid=paid, supplier_ref=ref, rest=rest,
        )
        return receipt

    def test_short_delivery_not_coming_paid_now(self):
        _result, order = self.order(5)
        receipt = self.receive(order, 4, "now", rest="not_coming", ref="KBL-778")
        self.assertEqual(receipt.state, "done")
        self.assertEqual(self.qty(self.tusker, self.loc_store), 96)
        self.assertEqual(receipt.backorder_ids.state, "cancel")
        bill = order.invoice_ids
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.ref, "KBL-778")
        self.assertEqual(bill.invoice_line_ids.quantity, 4)
        self.assertEqual(bill.invoice_line_ids.price_unit, 2400.0)
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        self.assertEqual(order.order_line.qty_received, 4)
        self.assertEqual(order.order_line.qty_invoiced, 4)

    def test_short_delivery_still_coming_pay_later(self):
        _result, order = self.order(5)
        receipt = self.receive(order, 3, "later")
        self.assertEqual(receipt.backorder_ids.state, "assigned", "the rest stays expected")
        receipts = self.desk_store.desk_receipts(self.store.id, self.token)
        self.assertEqual([r["id"] for r in receipts], receipt.backorder_ids.ids)
        bill = order.invoice_ids
        self.assertEqual(bill.payment_state, "not_paid")
        self.assertEqual(bill.invoice_line_ids.quantity, 3)

    def test_paid_now_needs_the_payment_account(self):
        self.store.supplier_payment_journal_id = False
        _result, order = self.order(1)
        with self.assertRaisesRegex(UserError, "account suppliers are paid from"):
            self.receive(order, 1, "now")
        self.assertEqual(order.picking_ids.state, "assigned", "nothing was received")

    def test_delivery_without_an_order(self):
        self.desk_store.desk_store_receive(
            self.store.id,
            self.token,
            self.uuid(),
            self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 2}],
            paid="later",
        )
        self.assertEqual(self.qty(self.tusker, self.loc_store), 48)
        bill = self.env["account.move"].search([("partner_id", "=", self.supplier.id), ("move_type", "=", "in_invoice")])
        self.assertEqual(bill.amount_untaxed, 4800.0)
        self.assertEqual(bill.payment_state, "not_paid")
