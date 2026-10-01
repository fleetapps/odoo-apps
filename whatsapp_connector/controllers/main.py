import hashlib
import hmac
import logging

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request

from odoo.addons.whatsapp_connector.models.whatsapp_account import WEBHOOK_ROUTE

_logger = logging.getLogger(__name__)


class WhatsappWebhook(http.Controller):
    """Meta's webhook endpoint (SPEC.md §32, §33, R26, R35).

    One Callback URL serves every account: Meta sends all of an app's WhatsApp
    Business Accounts to it, and each update names its account.
    """

    # csrf=False: Odoo checks a CSRF token on every POST to an http route, and
    # Meta cannot send one. The request is authenticated by its signature.
    @http.route(WEBHOOK_ROUTE, type="http", auth="public", methods=["GET", "POST"],
                csrf=False, save_session=False)
    def webhook(self, **kwargs):
        if request.httprequest.method == "GET":
            return self._verify(kwargs)
        return self._receive()

    def _verify(self, params):
        """Meta's verification request: echo hub.challenge when the token matches."""
        token = params.get("hub.verify_token") or ""
        if params.get("hub.mode") != "subscribe" or not token:
            return request.make_response("", status=403)
        tokens = request.env["whatsapp_connector.account"].sudo().search([
            ("webhook_verify_token", "!=", False),
        ]).mapped("webhook_verify_token")
        if not any(hmac.compare_digest(token, known) for known in tokens):
            return request.make_response("", status=403)
        return request.make_response(
            params.get("hub.challenge") or "", headers=[("Content-Type", "text/plain")],
        )

    def _receive(self):
        body = request.httprequest.get_data()  # raw bytes, before any parsing
        signature = request.httprequest.headers.get("X-Hub-Signature-256") or ""
        if not signature.startswith("sha256=") or not self._signature_valid(body, signature[7:]):
            _logger.warning("WhatsApp webhook: rejected a request with a missing or invalid signature")
            return request.make_response("", status=403)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return request.make_response("", status=400)
        env = request.env(su=True)
        env["whatsapp_connector.webhook.event"].create({"raw_body": text})
        env.ref("whatsapp_connector.ir_cron_process_webhook_events")._trigger()
        return request.make_response("", status=200)

    def _signature_valid(self, body, received):
        secrets = set(request.env["whatsapp_connector.account"].sudo().with_context(
            active_test=False,
        ).search([("app_secret", "!=", False)]).mapped("app_secret"))
        for secret in secrets:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, received):
                return True
        return False


class WhatsappDiscuss(http.Controller):
    """Discuss actions on WhatsApp messages."""

    @http.route("/whatsapp_connector/message/retry", type="jsonrpc", auth="user", methods=["POST"])
    def retry(self, message_id):
        """Send a failed or dropped message again (SPEC.md §35)."""
        message = request.env["mail.message"].browse(int(message_id)).exists()
        if not message or message.model != "discuss.channel":
            raise NotFound()
        # members of the conversation, and WhatsApp managers
        request.env["discuss.channel"].browse(message.res_id).check_access("read")
        # sudo: access to the conversation is checked above
        to_retry = message.sudo().wa_message_ids.filtered(
            lambda m: m.direction == "outbound" and m.status in ("failed", "dropped"),
        )
        to_retry.action_retry()
        return True
