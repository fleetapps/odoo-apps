from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged

from .common import BarDeskCase


@tagged("post_install", "-at_install")
class TestDeskTour(BarDeskCase, HttpCase):
    """The Desk in a real browser, on the stock controller's tablet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # start_tour signs in with the login as password.
        cls.tablet = new_test_user(
            cls.env,
            login="stock.tablet",
            groups="odin_bar_desk.group_bar_desk_staff",
            odin_bar_id=cls.store.id,
            odin_bar_ids=[Command.set((cls.bar_be | cls.bar_bb).ids)],
            action_id=cls.env.ref("odin_bar_desk.action_bar_desk").id,
            tz="Africa/Nairobi",
        )
        cls.mary.odin_bar_all = True
        cls.opening_stock(cls.loc_store, [(cls.tusker, 480)])
        cls.opening_stock(cls.loc_be, [(cls.jameson, 50), (cls.tusker, 48)])

    def test_move_tour(self):
        self.start_tour("/odoo", "odin_bar_desk_move", login="stock.tablet")
        picking = self.env["stock.picking"].search([("bar_employee_id", "=", self.mary.id)])
        self.assertTrue(picking.bar_manual_move)
        self.assertEqual(picking.location_id, self.loc_store)
        self.assertEqual(picking.location_dest_id, self.loc_be)
        self.assertEqual(picking.move_ids.product_uom, self.uom_crate)
        self.assertEqual(picking.move_ids.quantity, 2)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 48 + 48)

    def test_count_tour(self):
        self.start_tour("/odoo", "odin_bar_desk_count", login="stock.tablet")
        count = self.env["odin.bar.count"].search([("bar_id", "=", self.bar_be.id)])
        self.assertEqual(count.state, "submitted")
        self.assertEqual(count.employee_id, self.mary)
        counted = {line.product_id: line.counted_qty for line in count.line_ids}
        self.assertEqual(counted, {self.jameson: 37, self.tusker: 27, self.gordons: 0})
        reasons = {line.product_id: line.variance_reason_id.code for line in count.line_ids}
        self.assertEqual(reasons, {self.jameson: "spillage", self.tusker: "unexplained", self.gordons: False})
        self.assertTrue(count.line_ids.filtered(lambda line: line.product_id == self.gordons).accepted_expected)

    def test_supplier_delivery_tour(self):
        beer = self.env["product.category"].create({"name": "Beer, RTD & Cider"})
        # Tony West's price list is 100 a bottle before VAT; invoices show prices including it.
        vat = self.env["account.tax"].create({
            "name": "VAT 16% (purchase)", "amount": 16.0, "amount_type": "percent",
            "type_tax_use": "purchase", "company_id": self.company.id,
        })
        self.company.account_purchase_tax_id = vat
        self.tusker.write({"categ_id": beer.id, "supplier_taxes_id": [Command.set(vat.ids)]})
        supplier = self.env["res.partner"].create({"name": "Tony West", "is_company": True, "supplier_rank": 1})
        self.env["product.supplierinfo"].create(
            {"partner_id": supplier.id, "product_tmpl_id": self.tusker.product_tmpl_id.id, "price": 100.0}
        )
        self.store.supplier_payment_journal_id = self.env["account.journal"].search(
            [("type", "in", ("cash", "bank")), ("company_id", "=", self.company.id)], limit=1
        )
        self.start_tour("/odoo", "odin_bar_desk_supplier_delivery", login="stock.tablet")
        bill = self.env["account.move"].search([("partner_id", "=", supplier.id), ("move_type", "=", "in_invoice")])
        self.assertEqual(bill.state, "posted")
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        self.assertEqual((bill.amount_tax, bill.amount_total), (0.0, 22100.0), "this invoice had no VAT")
        cider = self.env["product.product"].search([("name", "=", "Tusker Cider 500ml")])
        self.assertTrue(cider.bar_desk_new)
        self.assertEqual(cider.list_price, 300.0)
        self.assertEqual(self.qty(cider, self.loc_store), 48)
        self.assertEqual(self.qty(self.tusker, self.loc_store), 480 + 120)
        self.assertEqual(supplier.bar_invoice_vat, "none")
