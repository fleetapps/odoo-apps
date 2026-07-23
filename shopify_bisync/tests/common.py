# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Shared fixtures. No test ever talks to the network: the instance's
``graphql`` / ``api_call_raw`` methods are patched at the registry class.

Testing framework reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/testing.html
"""
import json

from unittest.mock import patch

from odoo.tests import TransactionCase


def order_payload(**overrides):
    """A realistic orders/create webhook payload (REST shape)."""
    payload = {
        "id": 990001,
        "order_number": 1001,
        "_topic": "orders/create",
        "currency": "USD",
        "financial_status": "paid",
        "taxes_included": False,
        "total_tax": "3.87",
        "total_price": "66.33",
        "created_at": "2026-07-20T10:00:00-04:00",
        "note": "Leave at the door",
        "customer": {
            "id": 550001, "email": "jane@example.com",
            "first_name": "Jane", "last_name": "Doe",
            "default_address": {
                "address1": "1 Main St", "city": "Nairobi", "zip": "00100",
                "country_code": "KE", "province": "", "phone": "+254700000000",
            },
        },
        "billing_address": {"name": "Jane Doe", "address1": "1 Main St",
                            "city": "Nairobi", "zip": "00100",
                            "country_code": "KE"},
        "shipping_address": {"name": "Jane Doe", "address1": "1 Main St",
                             "city": "Nairobi", "zip": "00100",
                             "country_code": "KE"},
        "line_items": [{
            "id": 880001, "variant_id": 770001, "product_id": 660001,
            "title": "Widget", "sku": "WID-1", "quantity": 3,
            "price": "19.99",
            "discount_allocations": [{"amount": "5.01"}],
        }],
        "shipping_lines": [{"code": "standard", "title": "Standard",
                            "price": "7.50", "discounted_price": "7.50"}],
        "tax_lines": [{"title": "VAT", "rate": 0.16, "price": "3.87"}],
    }
    payload.update(overrides)
    return payload


class ShopifyBisyncCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(
            cls.env.context, tracking_disable=True, no_reset_password=True))
        cls.company = cls.env.company
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1)
        cls.instance = cls.env["shopify.bisync.instance"].create({
            "name": "Test Store",
            "shop_url": "test-store.myshopify.com",
            "access_token": "shpat_test",
            "webhook_secret": "shhh-secret",
            "warehouse_id": cls.warehouse.id,
        })
        cls.Job = cls.env["shopify.bisync.job"]
        cls.Binding = cls.env["shopify.bisync.binding"]
        cls.OrderSync = cls.env["shopify.bisync.order.sync"]
        cls.ProductSync = cls.env["shopify.bisync.product.sync"]
        cls.instance_cls = cls.env.registry["shopify.bisync.instance"]

    # ----------------------------------------------------------- utilities --
    def patch_graphql(self, side_effect):
        """Patch instance.graphql at the registry class; returns the mock."""
        patcher = patch.object(self.instance_cls, "graphql",
                               side_effect=side_effect, autospec=True)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def patch_rest(self, side_effect):
        patcher = patch.object(self.instance_cls, "api_call_raw",
                               side_effect=side_effect, autospec=True)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def run_jobs(self, batch=200):
        self.Job.cron_process(batch=batch)

    def make_product(self, name="Widget", sku="WID-1", price=25.0,
                     storable=True, barcode=None):
        template = self.env["product.template"].with_context(
            shopify_bisync_skip_trigger=True).create({
                "name": name, "default_code": sku, "list_price": price,
                "type": "consu", "is_storable": storable,
                "barcode": barcode,
            })
        return template

    def bind(self, record, external_id, inventory_item_id=None, **extra):
        return self.Binding.create({
            "instance_id": self.instance.id,
            "res_model": record._name,
            "res_id": record.id,
            "external_id": str(external_id),
            "inventory_item_id": inventory_item_id and str(inventory_item_id),
            **extra})

    def last_message(self, record):
        return self.env["mail.message"].search(
            [("model", "=", record._name), ("res_id", "=", record.id)],
            order="id desc", limit=1)


def gql_product_set_response(product_id=660001, variant_specs=None):
    """Build a productSet mutation response. variant_specs: list of dicts
    {id, sku, options: [(name, value)], inventory_item_id}."""
    variant_specs = variant_specs or [
        {"id": 770001, "sku": "WID-1", "options": [], "inventory_item_id": 440001}]
    return {"productSet": {
        "product": {
            "id": f"gid://shopify/Product/{product_id}",
            "updatedAt": "2026-07-21T12:00:00Z",
            "variants": {"nodes": [{
                "id": f"gid://shopify/ProductVariant/{v['id']}",
                "sku": v.get("sku"),
                "inventoryItem": {
                    "id": f"gid://shopify/InventoryItem/{v['inventory_item_id']}"},
                "selectedOptions": [
                    {"name": n, "value": val}
                    for n, val in (v.get("options")
                                   or [("Title", "Default Title")])],
            } for v in variant_specs]},
        },
        "userErrors": [],
    }}


def dumps(payload):
    return json.dumps(payload)
