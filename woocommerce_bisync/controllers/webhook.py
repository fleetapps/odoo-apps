# -*- coding: utf-8 -*-
"""Woo webhook receiver. Signature: X-WC-Webhook-Signature =
base64(HMAC-SHA256(raw_body, webhook secret)) per Woo webhook docs."""
import base64
import hashlib
import hmac
import json
from odoo import http
from odoo.http import request

TOPIC_KIND = {"order.created": "order", "order.updated": "order",
              "product.updated": "product", "customer.updated": "customer"}


class WooWebhook(http.Controller):

    @http.route("/woo_bisync/webhook/<int:instance_id>", type="http",
                auth="public", methods=["POST"], csrf=False, save_session=False)
    def webhook(self, instance_id, **kw):
        instance = request.env["woo.bisync.instance"].sudo().browse(
            instance_id).exists()
        if not instance:
            return request.make_json_response({}, status=404)
        raw = request.httprequest.get_data()
        if instance.webhook_secret:
            digest = base64.b64encode(hmac.new(
                instance.webhook_secret.encode(), raw,
                hashlib.sha256).digest()).decode()
            sig = request.httprequest.headers.get("X-WC-Webhook-Signature", "")
            if not hmac.compare_digest(digest, sig):
                return request.make_json_response({"error": "bad sig"}, status=401)
        topic = request.httprequest.headers.get("X-WC-Webhook-Topic", "")
        kind = TOPIC_KIND.get(topic)
        if kind:
            request.env["woo.bisync.job"].sudo().enqueue(
                instance, "in", kind, json.loads(raw or b"{}"))
        return request.make_json_response({"ok": True})
