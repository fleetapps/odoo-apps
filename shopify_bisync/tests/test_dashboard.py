# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Sales analytics: the SQL report view and the OWL dashboard aggregates."""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, order_payload


@tagged("post_install", "-at_install")
class TestDashboard(ShopifyBisyncCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.make_product(cls, name="Dash Widget", sku="DW-1")
        cls.variant = cls.template.product_variant_id
        cls.bind(cls, cls.variant, 770001, inventory_item_id=440001)

    def test_01_report_row_and_dashboard_data(self):
        self.instance.confirm_policy = "confirm"
        self.env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": self.variant.id,
            "location_id": self.warehouse.lot_stock_id.id,
            "inventory_quantity": 10}).action_apply_inventory()
        self.OrderSync._import_order(
            self.instance, order_payload(id=990401, order_number=1401))

        report = self.env["shopify.bisync.sale.report"].search(
            [("instance_id", "=", self.instance.id)])
        self.assertTrue(report, "confirmed Shopify order appears in analytics")
        self.assertEqual(report.mapped("product_tmpl_id"), self.template)

        data = self.env["shopify.bisync.sale.report"].dashboard_data()
        self.assertGreater(data["revenue"], 0.0)
        self.assertGreaterEqual(data["orders"], 1)
        product_names = [row["name"] for row in data["top_products"]]
        self.assertIn(self.template.display_name, product_names)
        instance_names = [row["name"] for row in data["by_instance"]]
        self.assertIn(self.instance.display_name, instance_names)
