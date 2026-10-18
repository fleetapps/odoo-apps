from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BarDeskCase


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
        offered = self.desk_store.desk_supplier_products(self.store.id, self.token, self.supplier.id)
        self.assertEqual(offered["ids"], self.tusker.ids, "their beer, not the spirits of another category")
        self.assertEqual(
            offered["prices"][f"{self.tusker.id}:{self.uom_crate.id}"],
            2400.0,
            "the Desk opens on what this supplier charges, not on a blank",
        )

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

    def test_invoice_total_differs_from_the_prices_entered(self):
        """A payment cannot be reconciled against a bill that is still in
        draft, so nothing is paid until a manager has checked it. Recording
        one anyway is how every bill on the live system came to read "not
        paid" while the money had gone out."""
        self.deliver(5, 5, paid="now", amount=12500)
        bill = self.bills()
        self.assertEqual(bill.state, "draft", "a manager checks the prices")
        self.assertEqual(bill.payment_state, "not_paid")
        self.assertFalse(
            self.env["account.payment"].search([("partner_id", "=", self.supplier.id)]),
            "no payment floats free of the bill it is meant to settle",
        )

    def test_the_receipt_is_backed_by_a_purchase_order(self):
        """Only a stock move carrying a purchase line is revalued when the
        bill is posted, and that revaluation is what moves the average cost."""
        self.deliver(5, 5, paid="later")
        order = self.env["purchase.order"].search([("partner_id", "=", self.supplier.id)])
        self.assertEqual(len(order), 1)
        self.assertEqual(order.state, "purchase")
        self.assertEqual(order.picking_ids.state, "done")
        self.assertTrue(all(move.purchase_line_id for move in order.picking_ids.move_ids))
        billed = self.bills().invoice_line_ids.filtered(lambda line: line.display_type == "product")
        self.assertTrue(all(line.purchase_line_id for line in billed))

    def test_the_price_typed_on_the_desk_is_what_the_goods_cost(self):
        """The price on the invoice in front of the staff member, not a price
        list that may be months out of date."""
        self.desk_store.desk_supplier_delivery(
            self.store.id,
            self.token,
            self.uuid(),
            self.supplier.id,
            [{
                "product_id": self.tusker.id, "uom_id": self.uom_crate.id,
                "invoiced": 2, "received": 2, "price": 3000.0,
            }],
            paid="now",
        )
        order = self.env["purchase.order"].search([("partner_id", "=", self.supplier.id)])
        self.assertEqual(order.order_line.price_unit, 3000.0, "not the 2,400 the price list says")
        bill = self.bills()
        self.assertEqual(bill.amount_untaxed, 6000.0)
        self.assertEqual(bill.state, "posted")
        self.assertIn(bill.payment_state, ("paid", "in_payment"))

    def test_receiving_and_paying_leaves_nothing_to_do(self):
        """One pass on the Desk: ordered, received, billed, posted and paid,
        with no draft waiting for anyone."""
        _activity, result = self.deliver(5, 5, paid="now", supplier_ref="TW-900")
        order = self.env["purchase.order"].search([("partner_id", "=", self.supplier.id)])
        bill = self.bills()
        self.assertEqual(order.state, "purchase")
        self.assertEqual(order.picking_ids.state, "done")
        self.assertEqual(bill.state, "posted")
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        self.assertTrue(result["bill"]["paid"])
        self.assertTrue(result["bill"]["checked"])
        self.assertFalse(
            self.env["account.move"].search([
                ("partner_id", "=", self.supplier.id), ("state", "=", "draft"),
            ]),
            "nothing is left in draft for a manager to finish",
        )

    def test_the_bill_carries_the_supplier_tax(self):
        """A purchase line created through a nested Command never runs Odoo's
        own tax deduction, so the taxes are mapped explicitly. Without that
        every bill the Desk raises would be VAT-free."""
        vat = self.env["account.tax"].create({
            "name": "VAT 16% (purchase)",
            "amount": 16.0,
            "amount_type": "percent",
            "type_tax_use": "purchase",
            "price_include_override": "tax_excluded",
            "company_id": self.company.id,
        })
        self.tusker.supplier_taxes_id = [(6, 0, vat.ids)]
        self.deliver(1, 1, paid="later")
        order = self.env["purchase.order"].search([("partner_id", "=", self.supplier.id)])
        self.assertEqual(order.order_line.tax_ids, vat, "the purchase line is taxed")
        bill = self.bills()
        self.assertEqual(bill.amount_untaxed, 2400.0)
        self.assertEqual(bill.amount_tax, 384.0)
        self.assertEqual(bill.amount_total, 2784.0)
        self.assertEqual(bill.state, "posted")

    def test_the_last_bill_prices_the_next_delivery(self):
        """With no price list entry, what the supplier last billed is a far
        better suggestion than a cost nobody has ever set."""
        self.deliver(2, 2, paid="later")
        self.env["product.supplierinfo"].search([("partner_id", "=", self.supplier.id)]).unlink()
        offered = self.desk_store.desk_supplier_products(self.store.id, self.token, self.supplier.id)
        self.assertEqual(offered["prices"][f"{self.tusker.id}:{self.uom_crate.id}"], 2400.0)

    def test_a_negative_price_is_refused(self):
        with self.assertRaisesRegex(UserError, "price cannot be negative"):
            self.desk_store.desk_supplier_delivery(
                self.store.id, self.token, self.uuid(), self.supplier.id,
                [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id,
                  "invoiced": 1, "received": 1, "price": -5.0}],
                paid="later",
            )

    def test_paid_now_needs_the_payment_account(self):
        self.store.supplier_payment_journal_id = False
        with self.assertRaisesRegex(UserError, "account suppliers are paid from"):
            self.deliver(1, 1, paid="now", amount=2400)
        self.assertEqual(self.qty(self.tusker, self.loc_store), 0)
