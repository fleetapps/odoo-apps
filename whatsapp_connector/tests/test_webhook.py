import hashlib
import hmac
import json

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from . import payloads
from .common import WhatsappCase

ROUTE = "/whatsapp_connector/webhook"


@tagged("post_install", "-at_install")
class TestWebhookEndpoint(HttpCase, WhatsappCase):
    """Meta's verification handshake and signed POSTs (R26, R35)."""

    def setUp(self):
        super().setUp()
        self.account._ensure_webhook_verify_token()
        self.token = self.account.sudo().webhook_verify_token

    def _sign(self, body, secret="test-app-secret"):
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def _post(self, body, signature):
        headers = {"Content-Type": "application/json"}
        if signature:
            headers["X-Hub-Signature-256"] = signature
        return self.url_open(ROUTE, data=body, headers=headers)

    def test_verification(self):
        ok = self.url_open(f"{ROUTE}?hub.mode=subscribe&hub.challenge=1158201444&hub.verify_token={self.token}")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.text, "1158201444")
        wrong = self.url_open(f"{ROUTE}?hub.mode=subscribe&hub.challenge=1&hub.verify_token=nope")
        self.assertEqual(wrong.status_code, 403)
        no_mode = self.url_open(f"{ROUTE}?hub.challenge=1&hub.verify_token={self.token}")
        self.assertEqual(no_mode.status_code, 403)

    def test_signed_post_is_stored_and_processing_triggered(self):
        body = json.dumps(payloads.inbound([payloads.text_message("wamid.A", "Hello")])).encode()
        Event = self.env["whatsapp_connector.webhook.event"]
        cron = self.env.ref("whatsapp_connector.ir_cron_process_webhook_events")
        triggers_before = self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)])
        response = self._post(body, self._sign(body))
        self.assertEqual(response.status_code, 200)
        event = Event.search([], limit=1)
        self.assertEqual(event.raw_body, body.decode())
        self.assertEqual(event.state, "new")
        self.assertGreater(
            self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)]), triggers_before,
        )

    @mute_logger("odoo.addons.whatsapp_connector.controllers.main")
    def test_unsigned_or_forged_post_rejected(self):
        body = json.dumps(payloads.inbound([payloads.text_message("wamid.B", "Hi")])).encode()
        Event = self.env["whatsapp_connector.webhook.event"]
        count = Event.search_count([])
        self.assertEqual(self._post(body, None).status_code, 403)
        self.assertEqual(self._post(body, self._sign(body, "wrong-secret")).status_code, 403)
        # a valid signature for a different body
        self.assertEqual(self._post(body + b" ", self._sign(body)).status_code, 403)
        self.assertEqual(Event.search_count([]), count)
