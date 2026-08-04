# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Match-before-create, guest-partner reuse and cross-store order identity.

These three between them are the "it duplicated everything" failure mode that
dominates the category's bad reviews, so each one gets a test that fails
loudly if the guard regresses.
"""
from odoo.tests import tagged

from .common import ShopifyBisyncCase, order_payload


def product_payload(**overrides):
    payload = {
        "id": 660501,
        "title": "Existing Widget",
        "status": "active",
        "updated_at": "2026-07-25T10:00:00Z",
        "images": [],
        "options": [],
        "variants": [{"id": 770501, "sku": "DUP-1", "price": "10.00",
                      "inventory_item_id": 440501}],
    }
    payload.update(overrides)
    return payload


@tagged("post_install", "-at_install")
class TestDeduplication(ShopifyBisyncCase):

    # -------------------------------------------------- match before create -
    def test_01_import_adopts_product_with_same_sku(self):
        existing = self.make_product(name="Local Widget", sku="DUP-1")
        before = self.env["product.template"].search_count([])
        self.ProductSync._import_product(self.instance, product_payload())
        self.assertEqual(self.env["product.template"].search_count([]), before,
                         "SKU match must adopt, not create a second product")
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("external_id", "=", "660501")])
        self.assertEqual(binding.res_id, existing.id)
        self.assertEqual(existing.name, "Existing Widget",
                         "adopted product takes the Shopify title")

    def test_02_import_adopts_on_barcode(self):
        existing = self.make_product(name="Barcoded", sku=None,
                                     barcode="5901234123457")
        self.ProductSync._import_product(self.instance, product_payload(
            variants=[{"id": 770502, "sku": None,
                       "barcode": "5901234123457", "price": "10.00"}]))
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("external_id", "=", "660501")])
        self.assertEqual(binding.res_id, existing.id)

    def test_03_import_adopts_on_exact_title(self):
        existing = self.make_product(name="Existing Widget", sku="OTHER-9")
        self.ProductSync._import_product(self.instance, product_payload(
            variants=[{"id": 770503, "sku": None, "price": "10.00"}]))
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("external_id", "=", "660501")])
        self.assertEqual(binding.res_id, existing.id)

    def test_04_ambiguous_match_creates_and_logs(self):
        self.make_product(name="Twin A", sku="AMB-1")
        self.make_product(name="Twin B", sku="AMB-1")
        before = self.env["product.template"].search_count([])
        self.ProductSync._import_product(self.instance, product_payload(
            variants=[{"id": 770504, "sku": "AMB-1", "price": "10.00"}]))
        self.assertEqual(self.env["product.template"].search_count([]),
                         before + 1,
                         "two candidates: never guess, create and flag")
        self.assertTrue(self.env["shopify.bisync.mismatch"].search_count(
            [("kind", "=", "match_ambiguous")]))

    def test_05_match_policy_off_always_creates(self):
        self.instance.product_match_policy = "off"
        self.make_product(name="Local Widget", sku="DUP-1")
        before = self.env["product.template"].search_count([])
        self.ProductSync._import_product(self.instance, product_payload())
        self.assertEqual(self.env["product.template"].search_count([]),
                         before + 1)

    def test_06_export_adopts_existing_shopify_product(self):
        tmpl = self.make_product(name="Export Me", sku="EXP-1")
        calls = []

        def fake(instance, query, variables=None):
            calls.append(query)
            if "variantMatch" in query:
                return {"productVariants": {"nodes": [{
                    "id": "gid://shopify/ProductVariant/770601",
                    "sku": "EXP-1", "barcode": None,
                    "product": {"id": "gid://shopify/Product/660601",
                                "legacyResourceId": "660601"}}]}}
            return {"productSet": {
                "product": {"id": "gid://shopify/Product/660601",
                            "updatedAt": "2026-07-25T10:00:00Z",
                            "media": {"nodes": []},
                            "variants": {"nodes": []}},
                "userErrors": []}}
        self.patch_graphql(fake)
        self.ProductSync._export_product(self.instance, {"res_id": tmpl.id})
        binding = self.Binding.search([("res_model", "=", "product.template"),
                                       ("res_id", "=", tmpl.id)])
        self.assertEqual(binding.external_id, "660601",
                         "export must bind to the existing listing")
        self.assertTrue(any("variantMatch" in c for c in calls))

    def test_07_search_literal_escapes(self):
        literal = self.ProductSync._search_literal('A:B "x" \\y')
        self.assertEqual(literal, '"A:B \\"x\\" \\\\y"')

    # ------------------------------------------------------- guest partners -
    def test_08_guest_orders_reuse_one_partner(self):
        anonymous = {"customer": {}, "billing_address": {"name": "Walk In"},
                     "shipping_address": {"name": "Walk In"}}
        self.OrderSync._import_order(self.instance, order_payload(
            id=990801, order_number=1801, **anonymous))
        self.OrderSync._import_order(self.instance, order_payload(
            id=990802, order_number=1802, **anonymous))
        first = self.env["sale.order"].search(
            [("client_order_ref", "=", "SHOPIFY/1801")]).partner_id
        second = self.env["sale.order"].search(
            [("client_order_ref", "=", "SHOPIFY/1802")]).partner_id
        self.assertEqual(first, second,
                         "anonymous checkouts must share one guest partner")
        self.assertEqual(self.env["res.partner"].search_count(
            [("name", "like", "Shopify Guest")]), 1)

    # ------------------------------------------------- cross-store identity -
    def test_09_same_order_number_in_two_stores(self):
        other = self.env["shopify.bisync.instance"].create({
            "name": "Second Store",
            "shop_url": "second-store.myshopify.com",
            "access_token": "shpat_test2",
            "warehouse_id": self.warehouse.id,
            "order_ref_prefix": "SHOP2",
        })
        self.OrderSync._import_order(
            self.instance, order_payload(id=990901, order_number=1901))
        self.OrderSync._import_order(
            other, order_payload(id=880901, order_number=1901))
        orders = self.env["sale.order"].search(
            [("shopify_bisync_order_id", "in", ("990901", "880901"))])
        self.assertEqual(len(orders), 2,
                         "same order number in two stores = two orders")
        self.assertEqual(
            set(orders.mapped("client_order_ref")),
            {"SHOPIFY/1901", "SHOP2/1901"})

    def test_10_redelivery_still_deduplicates(self):
        payload = order_payload(id=991001, order_number=2001)
        self.OrderSync._import_order(self.instance, payload)
        self.OrderSync._import_order(self.instance, payload)
        self.assertEqual(self.env["sale.order"].search_count(
            [("shopify_bisync_order_id", "=", "991001")]), 1)
