# -*- coding: utf-8 -*-
"""Stock movements, stock levels, supplier invoices and imports."""

from datetime import date
from unittest import SkipTest
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.x_ke_etims.backends.demo import DemoAdapter
from odoo.addons.x_ke_etims.tests.common import KeEtimsCase


@tagged("post_install", "-at_install")
class TestStock(KeEtimsCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.sudo().group_ids = [Command.link(cls.env.ref("stock.group_stock_manager").id)]
        cls.product.write({"is_storable": True, "standard_price": 1000.0})
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.ke_company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.supplier = cls.env["res.partner"].create({
            "name": "Unga Millers Ltd", "vat": "P000111222Z",
            "country_id": cls.env.ref("base.ke").id})

    def documents(self, kind=None):
        domain = [("company_id", "=", self.ke_company.id)]
        if kind:
            domain.append(("kind", "=", kind))
        return self.env["ke.etims.document"].search(domain, order="id")

    def move_stock(self, picking_type, source, destination, quantity, partner=None):
        picking = self.env["stock.picking"].with_company(self.ke_company).create({
            "picking_type_id": picking_type.id,
            "location_id": source.id,
            "location_dest_id": destination.id,
            "partner_id": partner.id if partner else False,
            "move_ids": [Command.create({
                "product_id": self.product.id,
                "product_uom_qty": quantity,
                "location_id": source.id,
                "location_dest_id": destination.id,
            })],
        })
        picking.action_confirm()
        picking.move_ids.write({"quantity": quantity, "picked": True})
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        return picking

    def receive(self, quantity=10.0):
        return self.move_stock(self.warehouse.in_type_id,
                               self.env.ref("stock.stock_location_suppliers"), self.stock,
                               quantity, partner=self.supplier)

    def test_a_receipt_is_a_purchase_movement_then_a_stock_level(self):
        picking = self.receive(10.0)
        document = self.documents("stock_io")
        self.assertEqual((document.res_model, document.res_id), ("stock.picking", picking.id))
        sale = picking._ke_etims_prepare(document)
        self.assertEqual(sale.io_type, "02")
        self.assertEqual(sale.customer_pin, "P000111222Z")
        self.assertEqual(float(sale.lines[0].quantity), 10.0)
        # Cost 1,000 at 16%, VAT included.
        self.assertEqual(float(sale.lines[0].unit_price), 1160.0)
        self.run_queue()
        self.assertEqual(document.state, "sent", document.last_error)
        self.assertEqual(document.kra_sequence_no, 1)
        master = self.documents("stock_master")
        self.assertEqual(master.res_id, self.product.id)
        self.run_queue()
        self.assertEqual(master.state, "sent", master.last_error)
        log = self.env["ke.etims.log"].search(
            [("document_id", "=", master.id), ("endpoint", "=", "save_stock_master")])
        self.assertIn('"rsdQty": "10.0"', log.request)
        # The next movement sends the same stock-level document again.
        self.receive(5.0)
        self.run_queue()
        self.run_queue()
        self.assertEqual(len(self.documents("stock_master")), 1)
        self.assertIn('"rsdQty": "15.0"', self.env["ke.etims.log"].search(
            [("document_id", "=", master.id)], limit=1).request)

    def test_movement_types_follow_the_locations(self):
        self.receive(10.0)
        customers = self.env.ref("stock.stock_location_customers")
        self.move_stock(self.warehouse.out_type_id, self.stock, customers, 3.0,
                        partner=self.customer)
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.product.id, "location_id": self.stock.id,
            "inventory_quantity": 5.0,  # 7 on hand after the delivery: a decrease
        }).action_apply_inventory()
        scrap = self.env["stock.scrap"].with_company(self.ke_company).create({
            "product_id": self.product.id, "scrap_qty": 1.0, "location_id": self.stock.id,
            "company_id": self.ke_company.id,
        })
        scrap.action_validate()
        types = [document._ke_etims_source()._ke_etims_prepare(document).io_type
                 for document in self.documents("stock_io")]
        self.assertEqual(types, ["02", "11", "16", "15"])

    def test_a_delivery_not_for_a_sale_is_sent_at_once(self):
        self.receive(10.0)
        self.move_stock(self.warehouse.out_type_id, self.stock,
                        self.env.ref("stock.stock_location_customers"), 2.0,
                        partner=self.customer)
        self.run_queue()
        self.assertEqual(set(self.documents("stock_io").mapped("state")), {"sent"})

    def test_an_internal_move_is_not_reported(self):
        self.receive(10.0)
        shelf = self.env["stock.location"].create({
            "name": "Shelf 9", "location_id": self.stock.id, "company_id": self.ke_company.id})
        self.move_stock(self.warehouse.int_type_id, self.stock, shelf, 4.0)
        self.assertEqual(len(self.documents("stock_io")), 1)

    def test_a_us_company_moves_stock_unreported(self):
        product = self.env["product.product"].create({"name": "US widget", "is_storable": True})
        us_stock = self.env["stock.warehouse"].search(
            [("company_id", "=", self.us_company.id)], limit=1).lot_stock_id
        self.env["stock.quant"].with_company(self.us_company).with_context(
            inventory_mode=True).create({
                "product_id": product.id, "location_id": us_stock.id,
                "inventory_quantity": 5.0}).action_apply_inventory()
        self.assertFalse(self.env["ke.etims.document"].search(
            [("company_id", "=", self.us_company.id)]))

    # ------------------------------------------------------------ purchases

    def supplier_sale(self, pin="P000111222Z", items=None):
        return {
            "supplier_pin": pin, "supplier_name": "Unga Millers Ltd", "supplier_bhf_id": "00",
            "supplier_invoice_no": 4711, "receipt_type": "P", "payment_type": "02",
            "sale_date": date(2026, 10, 20), "total_taxable": 2320, "total_tax": 320,
            "total_amount": 2320, "provider_ref": "%s/00/4711" % pin,
            "lines": items or [
                {"seq": 1, "item_code": "KE2NTBG0000009", "item_class_code": "5059690800",
                 "name": "Maize flour 2kg (Unga)", "quantity": 2, "unit_price": 1160,
                 "supply_amount": 2320, "discount_amount": 0, "tax_type": "B",
                 "taxable_amount": 2320, "tax_amount": 320, "total_amount": 2320},
            ],
        }

    def fetch(self, rows):
        with patch.object(DemoAdapter, "fetch_purchases", lambda adapter, since=None: rows):
            self.device._ke_etims_fetch_purchases()
        return self.env["ke.etims.purchase"].search([], order="id desc", limit=len(rows))

    def test_the_bill_carries_the_supplier_cu_number_where_kra_gives_it(self):
        row = dict(self.supplier_sale(), supplier_cu_invoice="KRACU0200000002/4711")
        purchase = self.fetch([row])
        self.assertEqual(purchase.supplier_cu_invoice, "KRACU0200000002/4711")
        self.assertEqual(purchase.move_id.ke_etims_supplier_invoice_ref, "KRACU0200000002/4711")

    def test_a_supplier_invoice_becomes_a_bill_and_is_confirmed(self):
        purchase = self.fetch([self.supplier_sale()])
        self.assertEqual(purchase.partner_id, self.supplier, "Matched by PIN.")
        bill = purchase.move_id
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.state, "draft")
        self.assertEqual(bill.ref, "4711")
        self.assertEqual(bill.ke_etims_supplier_invoice_ref, "P000111222Z/00/4711")
        self.assertEqual(bill.amount_total, 2320.0, "The supplier's VAT-inclusive total.")
        self.assertEqual(bill.amount_tax, 320.0)
        self.assertEqual(purchase.unmapped_count, 1)
        # Posting is not blocked, but KRA cannot be told about an item that is
        # not one of ours: the confirmation fails and says which.
        bill.action_post()
        self.run_queue()
        document = self.env["ke.etims.document"].search(
            [("res_model", "=", "ke.etims.purchase"), ("res_id", "=", purchase.id)])
        self.assertEqual(document.state, "failed")
        self.assertIn("Maize flour 2kg (Unga) is not linked to a product", document.last_error)

    def test_the_mapping_wizard_links_products_and_remembers(self):
        purchase = self.fetch([self.supplier_sale()])
        wizard = self.env["ke.etims.purchase.map"].with_context(
            default_purchase_id=purchase.id).create({})
        wizard.line_ids.product_id = self.product
        wizard.action_apply()
        self.assertEqual(purchase.line_ids.product_id, self.product)
        bill_line = purchase.move_id.invoice_line_ids
        self.assertEqual(bill_line.product_id, self.product)
        self.assertEqual(bill_line.price_unit, 1000.0, "Mapping must not reprice the line.")
        purchase.move_id.action_post()
        document = self.env["ke.etims.document"].search(
            [("res_model", "=", "ke.etims.purchase"), ("res_id", "=", purchase.id)])
        self.run_queue()
        self.assertEqual(document.state, "sent", document.last_error)
        self.assertEqual(purchase.state, "confirmed")
        self.assertEqual(document.kra_sequence_no, 1)
        data = purchase._ke_etims_prepare(document)
        self.assertEqual(data.supplier_invoice_no, 4711)
        self.assertEqual(data.lines[0].supplier_item_code, "KE2NTBG0000009")
        self.assertEqual(data.lines[0].item_code, self.product.ke_etims_item_code)
        # The next invoice from the same supplier maps itself.
        later = self.supplier_sale()
        later["provider_ref"] = "P000111222Z/00/4712"
        self.assertEqual(self.fetch([later]).line_ids.product_id, self.product)

    def test_an_unknown_supplier_waits_for_a_person(self):
        purchase = self.fetch([self.supplier_sale(pin="P999999999Z")])
        self.assertFalse(purchase.partner_id)
        self.assertFalse(purchase.move_id)
        with self.assertRaises(UserError):
            purchase.action_create_bill()
        purchase.partner_id = self.supplier
        purchase.action_create_bill()
        self.assertTrue(purchase.move_id)

    def test_a_purchase_is_fetched_once(self):
        self.fetch([self.supplier_sale()])
        self.fetch([self.supplier_sale()])
        self.assertEqual(self.env["ke.etims.purchase"].search_count([]), 1)

    # -------------------------------------------------------------- imports

    def test_an_import_is_linked_and_approved(self):
        row = {"task_code": "1141093", "declaration_date": date(2026, 8, 26), "item_seq": 1,
               "declaration_no": "C 4711", "hs_code": "84715000000",
               "name": "ETIMS INCOTEX 171", "quantity": 30, "supplier_name": "AL-AZHAR",
               "invoice_amount": 34800, "invoice_currency": "USD", "status": "2",
               "provider_ref": "1141093/1"}
        with patch.object(DemoAdapter, "fetch_imports", lambda adapter, since=None: [row]):
            self.device._ke_etims_fetch_imports()
        item = self.env["ke.etims.import"].search([("provider_ref", "=", "1141093/1")])
        self.assertEqual(item.state, "new")
        with self.assertRaises(UserError):
            item.action_approve()
        item.product_id = self.product
        item.action_approve()
        self.assertEqual(item.state, "approved")
        document = self.env["ke.etims.document"].search(
            [("res_model", "=", "ke.etims.import"), ("res_id", "=", item.id)])
        self.assertEqual(item._ke_etims_prepare(document).status, "3")


@tagged("post_install", "-at_install")
class TestDeliveryAfterInvoice(TestStock):
    """Only where sale_stock is installed: it is what links a delivery to its sale."""

    allow_inherited_tests_method = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "sale.order" not in cls.env or "sale_line_id" not in cls.env["stock.move"]._fields:
            raise SkipTest("sale_stock is not installed")
        cls.env.user.sudo().group_ids = [
            Command.link(cls.env.ref("sales_team.group_sale_manager").id)]

    def test_a_delivery_for_a_sale_waits_for_its_signed_invoice(self):
        self.receive(10.0)
        order = self.env["sale.order"].with_company(self.ke_company).create({
            "partner_id": self.customer.id,
            "company_id": self.ke_company.id,
            "order_line": [Command.create({"product_id": self.product.id,
                                           "product_uom_qty": 2.0})],
        })
        order.action_confirm()
        delivery = order.picking_ids
        delivery.move_ids.write({"quantity": 2.0, "picked": True})
        delivery.button_validate()
        document = self.env["ke.etims.document"].search(
            [("res_model", "=", "stock.picking"), ("res_id", "=", delivery.id)])
        self.run_queue()
        self.assertEqual(document.state, "to_send")
        self.assertIn("invoiced and signed", document.last_error)
        self.assertEqual(document.attempts, 0, "Waiting is not an attempt.")
        invoice = order._create_invoices()
        invoice.action_post()
        self.run_queue()  # the invoice is signed
        self.run_queue()  # and then the delivery goes
        self.assertEqual(invoice.ke_etims_state, "sent")
        self.assertEqual(document.state, "sent", document.last_error)
