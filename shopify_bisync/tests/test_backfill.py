# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""A4/A7: backfill - cursor pagination, page jobs spawning item jobs, chain
resumability (the cursor lives in durable jobs, not in the wizard)."""
import json

from odoo.tests import tagged

from .common import ShopifyBisyncCase


def product_node(pid, sku):
    return {
        "id": f"gid://shopify/Product/{pid}", "legacyResourceId": str(pid),
        "title": f"Backfill {sku}", "descriptionHtml": "",
        "status": "ACTIVE", "updatedAt": "2026-07-20T00:00:00Z",
        "options": [{"name": "Title", "position": 1,
                     "values": ["Default Title"]}],
        "media": {"nodes": []},
        "variants": {"nodes": [{
            "id": f"gid://shopify/ProductVariant/{pid + 1}",
            "legacyResourceId": str(pid + 1), "title": "Default Title",
            "sku": sku, "barcode": None, "price": "12.00",
            "compareAtPrice": None, "position": 1,
            "selectedOptions": [{"name": "Title", "value": "Default Title"}],
            "inventoryItem": {
                "id": f"gid://shopify/InventoryItem/{pid + 2}",
                "measurement": {"weight": {"value": 100, "unit": "GRAMS"}}},
        }]},
    }


@tagged("post_install", "-at_install")
class TestBackfill(ShopifyBisyncCase):

    def test_01_product_pages_chain_and_resume(self):
        pages = {
            None: {"products": {
                "pageInfo": {"hasNextPage": True, "endCursor": "CUR-1"},
                "nodes": [product_node(881001, "BF-1"),
                          product_node(881010, "BF-2")]}},
            "CUR-1": {"products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [product_node(881020, "BF-3")]}},
        }
        self.patch_graphql(
            lambda instance, query, variables=None:
            pages[(variables or {}).get("after")])
        wizard = self.env["shopify.bisync.backfill"].create({
            "instance_id": self.instance.id, "do_products": True})
        wizard.action_start()
        page_jobs = self.Job.search([("kind", "=", "backfill")])
        self.assertEqual(len(page_jobs), 1)
        self.run_jobs(batch=1)  # page 1 only - simulates a worker cutoff
        item_jobs = self.Job.search([("kind", "=", "product"),
                                     ("state", "=", "pending")])
        self.assertEqual(len(item_jobs), 2, "one import job per record")
        next_page = self.Job.search([("kind", "=", "backfill"),
                                     ("state", "=", "pending")])
        self.assertEqual(len(next_page), 1, "cursor persisted in a durable job")
        self.assertEqual(json.loads(next_page.payload_json)["cursor"], "CUR-1")
        # "Restart" the worker: just run the queue again - it resumes.
        self.run_jobs()
        self.run_jobs()  # items enqueued by page 2
        for sku in ("BF-1", "BF-2", "BF-3"):
            self.assertTrue(
                self.env["product.template"].search_count(
                    [("default_code", "=", sku)]),
                f"backfilled product {sku} imported")

    def test_02_orders_rest_cursor(self):
        link_next = ('<https://x.myshopify.com/admin/api/2026-07/orders.json'
                     '?limit=100&page_info=NEXTCUR>; rel="next"')
        responses = {
            None: ({"orders": [{"id": 995001, "order_number": 5001,
                                "line_items": [], "customer": None}]},
                   {"Link": link_next}),
            "NEXTCUR": ({"orders": [{"id": 995002, "order_number": 5002,
                                     "line_items": [], "customer": None}]},
                        {}),
        }
        self.patch_rest(
            lambda instance, method, endpoint, payload=None, params=None:
            responses[(params or {}).get("page_info")])
        wizard = self.env["shopify.bisync.backfill"].create({
            "instance_id": self.instance.id, "do_products": False,
            "do_orders": True})
        wizard.action_start()
        self.run_jobs(batch=1)  # first page
        order_jobs = self.Job.search([("kind", "=", "order"),
                                      ("state", "=", "pending")])
        self.assertEqual(len(order_jobs), 1)
        next_page = self.Job.search([("kind", "=", "backfill"),
                                     ("state", "=", "pending")])
        self.assertEqual(json.loads(next_page.payload_json)["page_info"],
                         "NEXTCUR")
        self.run_jobs()
        self.run_jobs()
        for number in (5001, 5002):
            self.assertTrue(self.env["sale.order"].search_count(
                [("client_order_ref", "=", f"SHOPIFY/{number}")]))

    def test_03_dry_run_counts_only(self):
        self.patch_graphql(
            lambda *a, **k: {"productsCount": {"count": 42}})
        wizard = self.env["shopify.bisync.backfill"].create({
            "instance_id": self.instance.id, "do_products": True})
        wizard.action_count()
        self.assertEqual(wizard.count_products, 42)
        self.assertEqual(wizard.state, "counted")
        self.assertFalse(self.Job.search_count([("kind", "=", "backfill")]),
                         "dry run must not enqueue anything")
