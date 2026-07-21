# -*- coding: utf-8 -*-
"""WooCommerce store instance.

Client: WooCommerce REST API v3 (wp-json/wc/v3) with consumer key/secret.
Official API docs: https://woocommerce.github.io/woocommerce-rest-api-docs/
Auth over HTTPS = basic auth with consumer_key/consumer_secret (per docs).
VERIFY-ON-BUILD: HPOS (High-Performance Order Storage) stores are the default
since Woo 8.2 - v3 REST is compatible, but test webhooks against an HPOS store.
"""
import logging
import requests
from odoo import fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class WooInstance(models.Model):
    _name = "woo.bisync.instance"
    _description = "WooCommerce Store"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    store_url = fields.Char(required=True, help="https://shop.example.com")
    consumer_key = fields.Char(required=True,
                               groups="woocommerce_bisync.group_connector_admin")
    consumer_secret = fields.Char(required=True,
                                  groups="woocommerce_bisync.group_connector_admin")
    webhook_secret = fields.Char(groups="woocommerce_bisync.group_connector_admin")
    company_id = fields.Many2one("res.company", required=True,
                                 default=lambda self: self.env.company)
    warehouse_id = fields.Many2one("stock.warehouse", required=True)
    sync_products = fields.Selection(
        [("off", "Off"), ("import", "Woo -> Odoo"),
         ("export", "Odoo -> Woo"), ("both", "Two-way")], default="both")
    sync_stock = fields.Selection(
        [("off", "Off"), ("export", "Odoo -> Woo"), ("both", "Two-way")],
        default="export")
    sync_orders = fields.Selection(
        [("off", "Off"), ("import", "Woo -> Odoo")], default="import")
    conflict_policy = fields.Selection(
        [("odoo_wins", "Odoo wins"), ("woo_wins", "WooCommerce wins"),
         ("newest_wins", "Most recent edit wins")], default="newest_wins")
    order_status_filter = fields.Char(
        default="processing,completed",
        help="Comma-separated Woo statuses to import as orders.")

    def _api(self, method, endpoint, payload=None, params=None):
        self.ensure_one()
        url = f"{self.store_url.rstrip('/')}/wp-json/wc/v3/{endpoint}"
        resp = requests.request(
            method, url, json=payload, params=params,
            auth=(self.consumer_key, self.consumer_secret), timeout=30)
        if resp.status_code >= 400:
            raise UserError(f"WooCommerce {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.text else {}

    def action_test_connection(self):
        self._api("GET", "system_status")
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"message": "Connection OK", "type": "success"}}

    def action_register_webhooks(self):
        """Woo webhooks: topic + delivery_url + secret.
        Docs: https://woocommerce.github.io/woocommerce-rest-api-docs/#webhooks"""
        base = self.get_base_url()
        for topic in ("order.created", "order.updated", "product.updated",
                      "customer.updated"):
            self._api("POST", "webhooks", {
                "name": f"Odoo {topic}", "topic": topic,
                "delivery_url": f"{base}/woo_bisync/webhook/{self.id}",
                "secret": self.webhook_secret or "",
            })
