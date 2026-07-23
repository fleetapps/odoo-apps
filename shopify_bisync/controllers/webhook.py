# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Shopify webhook receiver.

Controllers ref: https://www.odoo.com/documentation/19.0/developer/reference/backend/http.html

Security: every payload is verified with HMAC-SHA256 (base64) of the RAW body
against the app secret (``X-Shopify-Hmac-Sha256``) BEFORE any parsing, using a
constant-time compare. Invalid signature -> 401, nothing enqueued.

Latency: Shopify expects a 2xx within 5 seconds and deletes subscriptions
that keep failing (~19 retries over 48h). The handler therefore ONLY
enqueues a durable job and returns; all processing is async in the job cron.
"""
import base64
import hashlib
import hmac
import json

from odoo import http
from odoo.http import request

from ..models.instance import WEBHOOK_TOPICS

#: topic -> (job kind, priority). Webhooks outrank the stock sweep (20) and
#: backfill (45/50).
TOPIC_KIND = {
    "orders/create": ("order", 10),
    "orders/updated": ("order", 10),
    "orders/cancelled": ("order", 10),
    "products/create": ("product", 15),
    "products/update": ("product", 15),
    "inventory_levels/update": ("stock", 15),
    "customers/update": ("customer", 15),
    "refunds/create": ("refund", 10),
}
assert set(TOPIC_KIND) == set(WEBHOOK_TOPICS)


class ShopifyWebhook(http.Controller):

    @http.route("/shopify_bisync/webhook/<int:instance_id>", type="http",
                auth="public", methods=["POST"], csrf=False, save_session=False)
    def webhook(self, instance_id, **kw):
        instance = request.env["shopify.bisync.instance"].sudo().browse(
            instance_id).exists()
        if not instance or not instance.active:
            return request.make_json_response({}, status=404)
        raw = request.httprequest.get_data()
        digest = base64.b64encode(hmac.new(
            (instance.webhook_secret or "").encode(), raw,
            hashlib.sha256).digest()).decode()
        header = request.httprequest.headers.get("X-Shopify-Hmac-Sha256", "")
        if not instance.webhook_secret or not hmac.compare_digest(digest, header):
            return request.make_json_response({"error": "bad hmac"}, status=401)
        topic = request.httprequest.headers.get("X-Shopify-Topic", "")
        kind, priority = TOPIC_KIND.get(topic, (None, 10))
        if kind:
            payload = json.loads(raw or b"{}")
            payload["_topic"] = topic  # handlers branch on create/update/cancel
            request.env["shopify.bisync.job"].sudo().enqueue(
                instance, "in", kind, payload, priority=priority,
                lock_key=self._lock_key(kind, payload))
        # Unknown topics still return 200 so Shopify does not retry them.
        return request.make_json_response({"ok": True})

    @staticmethod
    def _lock_key(kind, payload):
        """Same Shopify object -> same key so its jobs never interleave."""
        if kind == "stock":
            return f"stock:item:{payload.get('inventory_item_id')}"
        if kind == "refund":
            return f"order:{payload.get('order_id')}"
        return f"{kind}:{payload.get('id')}"
