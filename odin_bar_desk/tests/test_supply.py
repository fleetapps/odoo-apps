from odoo.exceptions import UserError
from odoo.tests import tagged

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
class TestSupplierDelivery(BarDeskCase):
    """The store books a supplier delivery against the supplier's invoice."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        beer = cls.env["product.category"].create({"name": "Beer, RTD & Cider"})
        cls.tusker.write({"categ_id": beer.id, "supplier_taxes_id": [(5, 0, 0)]})
        cls.supplier = cls.env["res.partner"].create({"name": "Tony West", "is_company": True, "supplier_rank": 1})
        cls.env["res.partner"]._bar_seed_suppliers()
        cls.env["product.supplierinfo"].create(
            {"partner_id": cls.supplier.id, "product_tmpl_id": cls.tusker.product_tmpl_id.id, "price": 100.0}
        )
        cls.journal = cls.env["account.journal"].search(
            [("type", "in", ("cash", "bank")), ("company_id", "=", cls.company.id)], limit=1
        )
        cls.store.supplier_payment_journal_id = cls.journal

    def setUp(self):
        super().setUp()
        self.token = self.login(self.device_store, self.store, self.sam, "4321")
        self.desk_store = self.desk(self.device_store)

    def deliver(self, invoiced, received, **kwargs):
        result = self.desk_store.desk_supplier_delivery(
            self.store.id,
            self.token,
            self.uuid(),
            self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "invoiced": invoiced, "received": received}],
            **kwargs,
        )
        return self.env["odin.bar.activity"].browse(result["activity_id"]), result

    def bills(self, move_type="in_invoice"):
        return self.env["account.move"].search([("partner_id", "=", self.supplier.id), ("move_type", "=", move_type)])

    def test_suppliers_show_what_they_supply(self):
        self.assertEqual(self.supplier.bar_supplies, "Beers and spirits")
        suppliers = self.desk_store.desk_suppliers(self.store.id, self.token)
        info = next(s for s in suppliers if s["id"] == self.supplier.id)
        self.assertEqual(info["supplies"], "Beers and spirits")
        ids = self.desk_store.desk_supplier_products(self.store.id, self.token, self.supplier.id)
        self.assertEqual(ids, self.tusker.ids, "their beer, not the spirits of another category")

    def test_all_arrived_paid_now(self):
        activity, result = self.deliver(
            5, 5, paid="now", amount=12000, supplier_ref="TW-101",
            invoice={"name": "invoice.jpg", "mimetype": "image/jpeg", "data": "aGVsbG8="},
        )
        self.assertEqual(self.qty(self.tusker, self.loc_store), 120)
        bill = self.bills()
        self.assertEqual(bill.state, "posted")
        self.assertEqual(bill.ref, "TW-101")
        self.assertEqual(bill.amount_untaxed, 12000.0)
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        self.assertTrue(result["bill"]["paid"])
        attached = self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", bill.id)])
        self.assertEqual(attached.name, "invoice.jpg")

    def test_short_still_coming(self):
        self.deliver(5, 4, missing="coming", paid="later")
        self.assertEqual(self.qty(self.tusker, self.loc_store), 96)
        self.assertEqual(self.bills().invoice_line_ids.quantity, 5, "the bill follows the invoice")
        expected = self.desk_store.desk_receipts(self.store.id, self.token)
        self.assertEqual(len(expected), 1)
        self.assertTrue(expected[0]["billed"])
        detail = self.desk_store.desk_receipt(self.store.id, self.token, expected[0]["id"])
        self.assertEqual((detail["lines"][0]["qty"], detail["lines"][0]["uom_id"]), (1, self.uom_crate.id))
        # The last crate comes the next day: nothing more is billed.
        self.desk_store.desk_receipt_validate(
            self.store.id, self.token, self.uuid(), expected[0]["id"], {detail["lines"][0]["move_id"]: 1}, paid="now"
        )
        self.assertEqual(self.qty(self.tusker, self.loc_store), 120)
        self.assertEqual(len(self.bills()), 1)

    def test_short_credit_note(self):
        self.deliver(5, 4, missing="credit", paid="later")
        self.assertFalse(self.desk_store.desk_receipts(self.store.id, self.token))
        refund = self.bills("in_refund")
        self.assertEqual(refund.state, "draft", "waits for the supplier's credit note")
        self.assertEqual(refund.invoice_line_ids.quantity, 1)

    def test_invoice_total_differs_from_odoo_prices(self):
        self.deliver(5, 5, paid="now", amount=12500)
        bill = self.bills()
        self.assertEqual(bill.state, "draft", "a manager checks the prices")
        payment = self.env["account.payment"].search([("partner_id", "=", self.supplier.id)])
        self.assertEqual(payment.amount, 12500.0)
        self.assertNotEqual(payment.state, "draft")

    def test_paid_now_needs_the_payment_account(self):
        self.store.supplier_payment_journal_id = False
        with self.assertRaisesRegex(UserError, "account suppliers are paid from"):
            self.deliver(1, 1, paid="now", amount=2400)
        self.assertEqual(self.qty(self.tusker, self.loc_store), 0)
