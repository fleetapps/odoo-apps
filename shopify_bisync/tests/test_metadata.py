# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Metadata sync: order/customer/product tags, product category, tips,
duties, and product publishing."""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, gql_product_set_response, order_payload


@tagged("post_install", "-at_install")
class TestMetadata(ShopifyBisyncCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.make_product(cls, name="Meta Widget", sku="MW-1")
        cls.variant = cls.template.product_variant_id
        cls.bind(cls, cls.variant, 770001, inventory_item_id=440001)

    def _so(self, number):
        return self.env["sale.order"].search(
            [("client_order_ref", "=", f"SHOPIFY/{number}")])

    # ----------------------------------------------------------- order tags -
    def test_01_order_and_customer_tags(self):
        payload = order_payload(id=990201, order_number=1201,
                                tags="VIP, wholesale")
        payload["customer"]["tags"] = "loyal, newsletter"
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1201)
        self.assertEqual(sorted(so.tag_ids.mapped("name")),
                         ["VIP", "wholesale"])
        self.assertEqual(sorted(so.partner_id.category_id.mapped("name")),
                         ["loyal", "newsletter"])

    # --------------------------------------------------------- tips / duties -
    def test_02_tips_and_duties_lines(self):
        payload = order_payload(id=990202, order_number=1202)
        payload["total_tip_received"] = "4.00"
        payload["current_total_duties_set"] = {
            "shop_money": {"amount": "2.50", "currency_code": "USD"}}
        self.OrderSync._import_order(self.instance, payload)
        so = self._so(1202)
        tip_line = so.order_line.filtered(
            lambda l: l.product_id == self.instance.tip_product_id)
        duties_line = so.order_line.filtered(
            lambda l: l.product_id == self.instance.duties_product_id)
        self.assertEqual(f"{tip_line.price_unit:.2f}", "4.00")
        self.assertEqual(f"{duties_line.price_unit:.2f}", "2.50")

    def test_03_tips_duties_kept_in_shopify_total(self):
        self.instance.tax_policy = "shopify"
        payload = order_payload(id=990203, order_number=1203)
        payload["total_tip_received"] = "4.00"
        payload["total_price"] = "70.33"  # 66.33 + 4.00 tip
        self.OrderSync._import_order(self.instance, payload)
        self.assertEqual(f"{self._so(1203).amount_total:.2f}", "70.33")

    # ------------------------------------------------------ itemized taxes --
    def test_04_itemize_taxes_colorado_fee(self):
        self.instance.write({"tax_policy": "odoo", "itemize_taxes": True})
        payload = order_payload(id=990204, order_number=1204)
        payload["tax_lines"] = [
            {"title": "CO Retail Delivery Fee", "price": "0.29", "rate": 0}]
        self.OrderSync._import_order(self.instance, payload)
        fee_line = self._so(1204).order_line.filtered(
            lambda l: "Retail Delivery" in (l.name or ""))
        self.assertTrue(fee_line)
        self.assertEqual(f"{fee_line.price_unit:.2f}", "0.29")
        self.assertFalse(fee_line.tax_ids)

    # -------------------------------------------------------- product tags --
    def test_05_import_product_tags_and_category(self):
        self.instance.write({"sync_product_tags": True,
                             "sync_product_category": True})
        payload = {
            "id": 660500, "title": "Tagged", "body_html": "",
            "status": "active", "updated_at": "2026-07-21T10:00:00Z",
            "tags": "summer, sale", "product_type": "Apparel",
            "options": [{"name": "Title", "position": 1,
                         "values": ["Default Title"]}],
            "variants": [{"id": 771500, "sku": "TAG-1", "price": "9.99",
                          "inventory_item_id": 441500,
                          "option1": "Default Title", "grams": 100}],
            "images": [],
        }
        self.ProductSync._import_product(self.instance, payload)
        tmpl = self.Binding.get(self.env, self.instance, "product.template",
                                external_id=660500).resolve()
        self.assertEqual(sorted(tmpl.product_tag_ids.mapped("name")),
                         ["sale", "summer"])
        self.assertEqual(tmpl.categ_id.name, "Apparel")

    def test_06_export_includes_tags_and_type(self):
        tag = self.env["product.tag"].create({"name": "featured"})
        category = self.env["product.category"].create({"name": "Gadgets"})
        self.template.write({"product_tag_ids": [(6, 0, tag.ids)],
                            "categ_id": category.id})
        captured = {}

        def capture(instance, query, variables=None):
            captured.update(variables or {})
            return gql_product_set_response(variant_specs=[
                {"id": 770001, "sku": "MW-1", "inventory_item_id": 440001,
                 "options": []}])
        self.patch_graphql(capture)
        self.ProductSync._export_product(self.instance,
                                         {"res_id": self.template.id})
        self.assertIn("featured", captured["input"]["tags"])
        self.assertEqual(captured["input"]["productType"], "Gadgets")

    # ------------------------------------------------------------- publish --
    def test_07_auto_publish_enqueues_and_calls(self):
        self.bind(self.template, 660700)
        self.env["shopify.bisync.publication"].create({
            "instance_id": self.instance.id,
            "shopify_publication_id": "111", "name": "Online Store"})
        self.instance.publish_policy = "auto"
        calls = []

        def capture(instance, query, variables=None):
            calls.append(query)
            if "productSet" in query:
                return gql_product_set_response(variant_specs=[
                    {"id": 770001, "sku": "MW-1", "inventory_item_id": 440001,
                     "options": []}])
            return {"publishablePublish": {"userErrors": []}}
        self.patch_graphql(capture)
        self.ProductSync._export_product(self.instance,
                                         {"res_id": self.template.id})
        publish_jobs = self.Job.search([("kind", "=", "publish")])
        self.assertTrue(publish_jobs, "auto policy queues a publish job")
        publish_jobs._run_one() if len(publish_jobs) == 1 else None
        self.assertTrue(any("publishablePublish" in q for q in calls))
