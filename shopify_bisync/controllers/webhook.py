# -*- coding: utf-8 -*-
"""Shopify webhook receiver.

Controllers ref: https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html
Security: every payload verified with HMAC-SHA256(base64) of the raw body
against the app secret (X-Shopify-Hmac-Sha256) BEFORE any parsing. Invalid
signature -> 401. Handlers only enqueue; processing is async in the job cron.
"""
import base64
import hashlib
import hmac
import json
from odoo import http
from odoo.http import request

TOPIC_KIND = {
    "orders/create": "order", "orders/updated": "order",
    "orders/cancelled": "order", "products/update": "product",
    "inventory_levels/update": "stock", "customers/update": "customer",
    "refunds/create": "refund",
}


class ShopifyWebhook(http.Controller):

    @http.route("/shopify_bisync/webhook/<int:instance_id>", type="http",
                auth="public", methods=["POST"], csrf=False, save_session=False)
    def webhook(self, instance_id, **kw):
        instance = request.env["shopify.bisync.instance"].sudo().browse(
            instance_id).exists()
        if not instance:
            return request.make_json_response({}, status=404)
        raw = request.httprequest.get_data()
        digest = base64.b64encode(hmac.new(
            (instance.webhook_secret or "").encode(), raw,
            hashlib.sha256).digest()).decode()
        header = request.httprequest.headers.get("X-Shopify-Hmac-Sha256", "")
        if not hmac.compare_digest(digest, header):
            return request.make_json_response({"error": "bad hmac"}, status=401)
        topic = request.httprequest.headers.get("X-Shopify-Topic", "")
        kind = TOPIC_KIND.get(topic)
        if kind:
            direction = "in"
            request.env["shopify.bisync.job"].sudo().enqueue(
                instance, direction, kind, json.loads(raw or b"{}"))
        return request.make_json_response({"ok": True})
