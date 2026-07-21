# -*- coding: utf-8 -*-
"""Shopify instance (one record per store).

API client for Shopify Admin REST + GraphQL. Auth: private/custom app access
token (X-Shopify-Access-Token header).
VERIFY-ON-BUILD: pin the API version string against Shopify's release notes
(https://shopify.dev/docs/api/usage/versioning) - Shopify versions quarterly
and sunsets after 12 months. GraphQL is now the primary API for products.
"""
import logging
import requests
from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
API_VERSION = "2026-01"  # VERIFY-ON-BUILD


class ShopifyInstance(models.Model):
    _name = "shopify.bisync.instance"
    _description = "Shopify Store"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    shop_url = fields.Char(required=True, help="mystore.myshopify.com")
    access_token = fields.Char(required=True, groups="shopify_bisync.group_connector_admin")
    webhook_secret = fields.Char(groups="shopify_bisync.group_connector_admin",
                                 help="App's API secret used to verify webhook HMACs.")
    company_id = fields.Many2one("res.company", required=True,
                                 default=lambda self: self.env.company)
    warehouse_id = fields.Many2one("stock.warehouse", required=True)
    pricelist_id = fields.Many2one("product.pricelist")
    # ---- two-way sync policy (the differentiator: explicit direction per entity) ----
    sync_products = fields.Selection(
        [("off", "Off"), ("import", "Shopify -> Odoo"),
         ("export", "Odoo -> Shopify"), ("both", "Two-way")], default="both")
    sync_stock = fields.Selection(
        [("off", "Off"), ("export", "Odoo -> Shopify"),
         ("import", "Shopify -> Odoo"), ("both", "Two-way")], default="export")
    sync_prices = fields.Selection(
        [("off", "Off"), ("export", "Odoo -> Shopify")], default="export")
    sync_orders = fields.Selection(
        [("off", "Off"), ("import", "Shopify -> Odoo")], default="import")
    conflict_policy = fields.Selection(
        [("odoo_wins", "Odoo wins"), ("shopify_wins", "Shopify wins"),
         ("newest_wins", "Most recent edit wins")],
        default="newest_wins",
        help="Applied when the same record changed on both sides between syncs.")
    last_import_orders = fields.Datetime()
    last_export_stock = fields.Datetime()

    # ------------------------------------------------------------- API client
    def _base(self):
        return f"https://{self.shop_url}/admin/api/{API_VERSION}"

    def _headers(self):
        return {"X-Shopify-Access-Token": self.access_token,
                "Content-Type": "application/json"}

    def api_call(self, method, endpoint, payload=None, params=None):
        """REST call with 429 rate-limit backoff (Shopify leaky bucket)."""
        self.ensure_one()
        url = f"{self._base()}/{endpoint}"
        for attempt in range(5):
            resp = requests.request(method, url, json=payload, params=params,
                                    headers=self._headers(), timeout=30)
            if resp.status_code == 429:
                import time
                time.sleep(float(resp.headers.get("Retry-After", "2")))
                continue
            if resp.status_code >= 400:
                raise UserError(f"Shopify {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.text else {}
        raise UserError("Shopify rate limit: retries exhausted")

    def action_test_connection(self):
        self.api_call("GET", "shop.json")
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": "Connection OK", "type": "success"}}

    def action_register_webhooks(self):
        """Create the inbound webhooks that make two-way sync real-time.
        Topics ref: https://shopify.dev/docs/api/admin-rest/latest/resources/webhook"""
        base = self.get_base_url()
        for topic in ("orders/create", "orders/updated", "orders/cancelled",
                      "products/update", "inventory_levels/update",
                      "customers/update", "refunds/create"):
            self.api_call("POST", "webhooks.json", {
                "webhook": {"topic": topic,
                            "address": f"{base}/shopify_bisync/webhook/{self.id}",
                            "format": "json"}})
