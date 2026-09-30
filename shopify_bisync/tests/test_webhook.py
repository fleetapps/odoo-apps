# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""A7: webhook HMAC - valid signature -> 200 + job enqueued; tampered body
-> 401 and NOTHING enqueued."""
import base64
import hashlib
import hmac
import json

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestWebhookHmac(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1)
        cls.instance = cls.env["shopify.bisync.instance"].create({
            "name": "HMAC Store", "shop_url": "hmac.myshopify.com",
            "access_token": "shpat_test", "webhook_secret": "topsecret",
            "warehouse_id": warehouse.id,
        })

    def _post(self, body, secret="topsecret", topic="orders/create"):
        digest = base64.b64encode(hmac.new(
            secret.encode(), body, hashlib.sha256).digest()).decode()
        return self.url_open(
            f"/shopify_bisync/webhook/{self.instance.id}", data=body,
            headers={"X-Shopify-Hmac-Sha256": digest,
                     "X-Shopify-Topic": topic,
                     "Content-Type": "application/json"})

    def test_01_valid_hmac_enqueues(self):
        body = json.dumps({"id": 1, "order_number": 7}).encode()
        response = self._post(body)
        self.assertEqual(response.status_code, 200)
        job = self.env["shopify.bisync.job"].search(
            [("instance_id", "=", self.instance.id)])
        self.assertEqual(len(job), 1)
        self.assertEqual((job.direction, job.kind, job.state),
                         ("in", "order", "pending"))
        payload = json.loads(job.payload_json)
        self.assertEqual(payload["_topic"], "orders/create")

    def test_02_tampered_body_rejected(self):
        body = json.dumps({"id": 2}).encode()
        digest = base64.b64encode(hmac.new(
            b"topsecret", body, hashlib.sha256).digest()).decode()
        response = self.url_open(
            f"/shopify_bisync/webhook/{self.instance.id}",
            data=body + b"tampered",  # body altered after signing
            headers={"X-Shopify-Hmac-Sha256": digest,
                     "X-Shopify-Topic": "orders/create",
                     "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.env["shopify.bisync.job"].search_count(
            [("instance_id", "=", self.instance.id)]))

    def test_03_wrong_secret_rejected(self):
        response = self._post(b"{}", secret="not-the-secret")
        self.assertEqual(response.status_code, 401)

    def test_04_unknown_topic_returns_200_without_job(self):
        response = self._post(b"{}", topic="themes/publish")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.env["shopify.bisync.job"].search_count(
            [("instance_id", "=", self.instance.id)]))
