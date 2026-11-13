from unittest.mock import patch

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
        self.deliver(1, 1, paid="later", vat="added")
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

    # ------------------------------------------------------------------
    # What the Desk has to get right once it posts and pays by itself
    # ------------------------------------------------------------------

    def line(self, invoiced, received=None, price=2400.0, product=None, uom=None):
        return {
            "product_id": (product or self.tusker).id,
            "uom_id": (uom or self.uom_crate).id,
            "invoiced": invoiced,
            "received": invoiced if received is None else received,
            "price": price,
        }

    def deliver_lines(self, lines, **kwargs):
        kwargs.setdefault("paid", "later")
        return self.desk_store.desk_supplier_delivery(
            self.store.id, self.token, self.uuid(), self.supplier.id, lines, **kwargs
        )

    def test_crates_are_booked_as_bottles(self):
        """Odoo books a receipt in the product's own unit, not the crates
        the invoice is in: five crates of 24 are 120 bottles, not five."""
        self.deliver_lines([self.line(5)])
        self.assertEqual(self.qty(self.tusker, self.loc_store), 120)
        self.assertFalse(self.desk_store.desk_receipts(self.store.id, self.token), "nothing left expected")

    def test_two_step_approval_still_receives(self):
        """Odoo 19's button_approve drops an order the user may not approve,
        whatever force says. The goods are on the counter: they go in."""
        self.company.write({"po_double_validation": "two_step", "po_double_validation_amount": 0})
        self.deliver_lines([self.line(5)])
        order = self.env["purchase.order"].search([("partner_id", "=", self.supplier.id)])
        self.assertEqual(order.state, "purchase")
        self.assertEqual(order.picking_ids.state, "done")
        self.assertEqual(self.qty(self.tusker, self.loc_store), 120)

    def test_an_invoiced_line_needs_a_price(self):
        with self.assertRaisesRegex(UserError, "Type what Tony West charged for Tusker"):
            self.deliver_lines([self.line(5, price=0)])
        self.assertEqual(self.qty(self.tusker, self.loc_store), 0)
        self.assertFalse(self.bills())

    def test_the_last_price_paid_beats_a_stale_price_list(self):
        """Confirming an order puts the supplier on the price list at that
        first price and never updates it; what was paid last is the truth."""
        self.deliver_lines([self.line(2, price=3000.0)])
        offered = self.desk_store.desk_supplier_products(self.store.id, self.token, self.supplier.id)
        self.assertEqual(offered["prices"][f"{self.tusker.id}:{self.uom_crate.id}"], 3000.0)
        self.assertEqual(offered["prices"][f"{self.tusker.id}:{self.uom_unit.id}"], 125.0)

    def test_every_product_gets_a_suggestion(self):
        """"Other products" is one tap away; a blank there is how zeros got in."""
        offered = self.desk_store.desk_supplier_products(self.store.id, self.token, self.supplier.id)
        self.assertNotIn(self.jameson.id, offered["ids"])
        self.assertEqual(offered["prices"][f"{self.jameson.id}:{self.uom_750.id}"], 2000.0, "25 tots at 80")
        self.assertEqual(self.desk_store.desk_supplier_products(self.store.id, self.token, 0), {"ids": [], "prices": {}})

    def _vat(self):
        vat = self.env["account.tax"].create({
            "name": "VAT 16% (purchase)",
            "amount": 16.0,
            "amount_type": "percent",
            "type_tax_use": "purchase",
            "price_include_override": "tax_excluded",
            "company_id": self.company.id,
        })
        self.tusker.supplier_taxes_id = [(6, 0, vat.ids)]
        return vat

    def test_prices_that_include_vat_bill_exactly_the_invoice(self):
        """Six crates at 1,000 including VAT is 6,000 -- not 6,000.01 from
        rounding a net price, and not 6,960 from adding VAT again."""
        vat = self._vat()
        self.tusker.categ_id.with_company(self.company).property_cost_method = "average"
        self.deliver_lines([self.line(6, price=1000.0)], vat="included", paid="now")
        bill = self.bills()
        self.assertEqual(bill.amount_total, 6000.0)
        self.assertAlmostEqual(bill.amount_tax, 827.59, places=2)
        line_tax = bill.invoice_line_ids.tax_ids
        self.assertTrue(line_tax.price_include)
        self.assertEqual((line_tax.amount, line_tax.tax_group_id), (vat.amount, vat.tax_group_id))
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        self.assertEqual(self.supplier.bar_invoice_vat, "included", "remembered for next time")
        # The stock is valued before VAT, which is reclaimed.
        self.assertAlmostEqual(self.tusker.with_company(self.company).standard_price, 5172.41 / 144, places=2)
        # The twin tax is made once and reused.
        self.deliver_lines([self.line(1, price=1000.0)])
        self.assertEqual(self.bills().invoice_line_ids.tax_ids, line_tax)

    def test_a_supplier_without_vat_is_billed_without_vat(self):
        self._vat()
        self.deliver_lines([self.line(2, price=2400.0)], vat="none")
        bill = self.bills()
        self.assertEqual((bill.amount_tax, bill.amount_total), (0.0, 4800.0))
        self.assertEqual(self.supplier.bar_invoice_vat, "none")
        suppliers = self.desk_store.desk_suppliers(self.store.id, self.token, "Tony")
        self.assertEqual(suppliers[0]["vat"], "none", "the next delivery opens on it")

    def test_vat_included_is_the_default(self):
        """Drinks and cigarettes are invoiced in Kenya at prices including
        VAT: 2,400 on the invoice is 2,400 to pay, of which VAT is 331.03."""
        self._vat()
        self.assertEqual(self.supplier.bar_invoice_vat, "included")
        self.deliver_lines([self.line(1, price=2400.0)])
        bill = self.bills()
        self.assertEqual(bill.amount_total, 2400.0)
        self.assertAlmostEqual(bill.amount_tax, 331.03, places=2)

    def test_vat_added_on_top(self):
        self._vat()
        self.deliver_lines([self.line(1, price=2400.0)], vat="added")
        self.assertEqual(self.bills().amount_total, 2784.0)
        self.assertEqual(self.supplier.bar_invoice_vat, "added", "remembered for next time")

    def test_the_cost_follows_what_was_paid_on_average_cost(self):
        beer = self.tusker.categ_id.with_company(self.company)
        beer.property_cost_method = "average"
        tusker = self.tusker.with_company(self.company)
        result = self.desk_store.desk_supplier_delivery(
            self.store.id, self.token, self.uuid(), self.supplier.id, [self.line(5, price=3000.0)], paid="now"
        )
        self.assertAlmostEqual(tusker.standard_price, 125.0, msg="3,000 a crate of 24, not the old 150")
        self.assertEqual(result["new_costs"], [tusker.display_name])
        self.deliver_lines([self.line(5, price=3600.0)])
        self.assertAlmostEqual(tusker.standard_price, 137.5, msg="120 at 125 and 120 at 150")

    def test_the_cost_follows_what_was_paid_on_fifo(self):
        """First in, first out: what goes out is valued at the price of the
        oldest stock still on the shelf, not at a blend."""
        beer = self.tusker.categ_id.with_company(self.company)
        beer.property_cost_method = "fifo"
        tusker = self.tusker.with_company(self.company)
        self.deliver_lines([self.line(5, price=3000.0)])
        self.deliver_lines([self.line(5, price=3600.0)])
        self.assertAlmostEqual(tusker.standard_price, 137.5, msg="what is on the shelf, on average")
        sold = self.post(self.type_sal_be, self.loc_store, self.loc_customers, [(self.tusker, 120, None)])
        self.assertAlmostEqual(sold.move_ids.value, 120 * 125.0, msg="the first 120 cost 125 each")
        self.assertAlmostEqual(tusker.standard_price, 150.0, msg="only the 150s are left")

    def test_perpetual_valuation_books_the_bill_to_stock(self):
        """With perpetual valuation the bill debits stock rather than an
        expense, and the payment settles the bill it was made for."""
        stock_account = self.env["account.account"].create({
            "name": "Bar stock", "code": "100199", "account_type": "asset_current",
        })
        beer = self.tusker.categ_id.with_company(self.company)
        beer.write({
            "property_valuation": "real_time",
            "property_cost_method": "fifo",
            "property_stock_valuation_account_id": stock_account.id,
        })
        self.deliver_lines([self.line(5, price=3000.0)], paid="now")
        bill = self.bills()
        product_line = bill.invoice_line_ids
        self.assertEqual(product_line.account_id, stock_account)
        self.assertEqual(product_line.balance, 15000.0)
        self.assertEqual(bill.payment_state if bill.payment_state != "in_payment" else "paid", "paid")
        payment = self.env["account.payment"].search([("partner_id", "=", self.supplier.id)])
        self.assertEqual(payment.amount, bill.amount_total)
        self.assertIn(bill, payment.reconciled_bill_ids)

    def test_paid_now_is_booked_without_an_outstanding_account(self):
        """A payment journal set up by hand has no outstanding account on its
        payment method, and with the Accountant app Odoo then leaves a payment
        out of the books until a bank statement matches it. Paying must still
        book the payment and settle the bill, or the books keep owing a
        supplier who was paid."""
        self.journal.outbound_payment_method_line_ids.payment_account_id = False
        with patch.object(
            self.env.registry["account.move"], "_get_invoice_in_payment_state", lambda self: "in_payment"
        ):
            self.deliver_lines([self.line(5, price=3000.0)], paid="now")
        bill = self.bills()
        payment = self.env["account.payment"].search([("partner_id", "=", self.supplier.id)])
        self.assertEqual(payment.move_id.state, "posted", "the payment is in the books")
        self.assertTrue(bill.currency_id.is_zero(bill.amount_residual), "and the bill no longer owed")
        self.assertIn(bill, payment.reconciled_bill_ids)

    def test_a_price_jump_is_called_out_on_the_bill(self):
        self.deliver_lines([self.line(1, price=2400.0)])
        self.deliver_lines([self.line(1, price=24000.0)])
        bill = self.bills()[:1]
        notes = " ".join(bill.message_ids.mapped("body"))
        self.assertIn("Check these prices", notes)
        self.assertIn("was", notes)

    # ------------------------------------------------------------------
    # A product the store has never stocked
    # ------------------------------------------------------------------

    def new_line(self, name="Tusker Cider 500ml", sale_price=300.0, **extra):
        return {
            "product_id": False,
            "new_product": dict(
                {
                    "name": name,
                    "categ_id": self.tusker.categ_id.id,
                    "uom_id": self.uom_unit.id,
                    "pack_uom_id": self.uom_crate.id,
                    "sale_price": sale_price,
                },
                **extra,
            ),
            "uom_id": self.uom_crate.id,
            "invoiced": 2,
            "received": 2,
            "price": 4800.0,
        }

    def test_a_new_product_is_made_while_receiving(self):
        self.deliver_lines([self.new_line()], paid="now")
        cider = self.env["product.product"].search([("name", "=", "Tusker Cider 500ml")])
        self.assertEqual(len(cider), 1)
        self.assertTrue(cider.bar_desk_ok and cider.bar_desk_new and cider.is_storable)
        self.assertEqual(cider.categ_id, self.tusker.categ_id)
        self.assertEqual((cider.uom_id, cider.uom_ids), (self.uom_unit, self.uom_crate))
        self.assertFalse(cider.bar_bottle_uom_id, "a crate is a pack, not a bottle to count in")
        self.assertEqual(cider.list_price, 300.0)
        self.assertEqual(cider.company_id, self.company)
        self.assertEqual(self.qty(cider, self.loc_store), 48)
        # It takes the company's purchase VAT, and the 4,800 a crate typed
        # includes it: the cost is what is left once VAT is reclaimed.
        self.assertEqual(cider.supplier_taxes_id, self.company.account_purchase_tax_id)
        vat = 1 + cider.supplier_taxes_id.amount / 100
        self.assertAlmostEqual(cider.with_company(self.company).standard_price, 200.0 / vat, places=2)
        self.assertEqual(self.bills().amount_total, 9600.0)
        self.assertIn(cider, self.store._desk_products(), "on the Desk from now on")
        self.assertIn("Added on the Bar Desk by Sam", cider.product_tmpl_id.message_ids[:1].body)

    def test_a_new_spirit_is_kept_in_tots(self):
        whiskey = self.env["product.category"].create({"name": "Irish, Tennessee & Bourbon"})
        self.jameson.categ_id = whiskey
        line = self.new_line(
            "Jameson Caskmates", sale_price=350.0, categ_id=whiskey.id,
            uom_id=self.uom_tot.id, pack_uom_id=self.uom_750.id,
        )
        line.update(uom_id=self.uom_750.id, invoiced=6, received=6, price=2500.0)
        self.deliver_lines([line])
        caskmates = self.env["product.product"].search([("name", "=", "Jameson Caskmates")])
        self.assertEqual(caskmates.uom_id, self.uom_tot)
        self.assertEqual(caskmates.bar_bottle_uom_id, self.uom_750)
        self.assertEqual(self.qty(caskmates, self.loc_store), 150)
        vat = 1 + caskmates.supplier_taxes_id.amount / 100
        self.assertAlmostEqual(caskmates.with_company(self.company).standard_price, 100.0 / vat, places=2)

    def test_a_new_product_needs_its_selling_price(self):
        with self.assertRaisesRegex(UserError, "selling price"):
            self.deliver_lines([self.new_line(sale_price=0)])
        self.assertFalse(self.env["product.product"].search([("name", "=", "Tusker Cider 500ml")]))

    def test_a_new_product_takes_a_shape_its_category_uses(self):
        with self.assertRaisesRegex(UserError, "how Tusker Cider 500ml is counted"):
            self.deliver_lines([self.new_line(uom_id=self.uom_tot.id, pack_uom_id=False)])
        with self.assertRaisesRegex(UserError, "not a unit"):
            self.deliver_lines([self.new_line(pack_uom_id=self.uom_750.id)])
        with self.assertRaisesRegex(UserError, "category"):
            self.deliver_lines([self.new_line(categ_id=self.env["product.category"].create({"name": "Cars"}).id)])

    def test_a_name_already_on_the_desk_is_that_product(self):
        """Typed again on another invoice before the Desk reloaded, the same
        name must not become a second product."""
        self.deliver_lines([self.new_line(name="tusker  LAGER 500ml")])
        self.assertEqual(
            self.env["product.product"].search_count([("name", "ilike", "tusker lager 500ml")]), 1
        )
        self.assertEqual(self.qty(self.tusker, self.loc_store), 48)

    def test_the_same_invoice_is_not_paid_twice(self):
        """Odoo only flags a repeated vendor reference. With the bill posted
        and paid in one tap, the Desk refuses it outright."""
        self.deliver_lines([self.line(1)], paid="now", supplier_ref="TW-77")
        with self.assertRaisesRegex(UserError, "Invoice tw-77 from Tony West is already in"):
            self.deliver_lines([self.line(1)], paid="now", supplier_ref=" tw-77 ")
        self.assertEqual(len(self.bills()), 1)
        self.assertEqual(self.qty(self.tusker, self.loc_store), 24)
        # A reference is matched as typed, not as a pattern.
        self.deliver_lines([self.line(1)], supplier_ref="TW_7")
        self.deliver_lines([self.line(1)], supplier_ref="TWX7")
        self.deliver_lines([self.line(1)])
        self.deliver_lines([self.line(1)])
        self.assertEqual(len(self.bills()), 5)
