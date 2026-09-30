from unittest.mock import MagicMock, patch

from odoo.tests import tagged

from .common import WhatsappCase
from odoo.addons.whatsapp_connector.tools.meta_api import MetaApiError, WhatsAppApi

REQUEST = "odoo.addons.whatsapp_connector.tools.meta_api.requests.request"


def fake_response(status=200, payload=None):
    response = MagicMock()
    response.status_code = status
    response.ok = 200 <= status < 300
    response.json.return_value = payload if payload is not None else {}
    return response


@tagged("post_install", "-at_install")
class TestMetaApi(WhatsappCase):
    """The service layer (§55, R23) and Test Connection (§34, R24)."""

    def test_send_message_and_token_header(self):
        api = WhatsAppApi(self.account)
        with patch(REQUEST, return_value=fake_response(200, {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "+16505551234", "wa_id": "16505551234"}],
            "messages": [{"id": "wamid.OUT"}],
        })) as request:
            result = api.send_message({"to": "+16505551234", "type": "text", "text": {"body": "Hi"}})
        self.assertEqual(result["messages"][0]["id"], "wamid.OUT")
        method, url = request.call_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://graph.facebook.com/v25.0/106540352242922/messages")
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-access-token")
        self.assertEqual(kwargs["json"]["messaging_product"], "whatsapp")
        self.assertEqual(kwargs["json"]["recipient_type"], "individual")

    def test_error_parsing(self):
        api = WhatsAppApi(self.account)
        with patch(REQUEST, return_value=fake_response(401, {"error": {
            "message": "Error validating access token: Session has expired",
            "type": "OAuthException", "code": 190, "fbtrace_id": "AXsgn",
        }})), self.assertRaises(MetaApiError) as caught:
            api.get_phone_number()
        self.assertTrue(caught.exception.is_token_error)
        self.assertNotIn("test-access-token", str(caught.exception))

    def test_template_pagination(self):
        api = WhatsAppApi(self.account)
        pages = [
            fake_response(200, {"data": [{"name": "a"}], "paging": {"cursors": {"after": "X"}, "next": "https://n"}}),
            fake_response(200, {"data": [{"name": "b"}], "paging": {"cursors": {"after": "Y"}}}),
        ]
        with patch(REQUEST, side_effect=pages):
            self.assertEqual([t["name"] for t in api.get_templates()], ["a", "b"])

    def _test_connection(self, number, apps, error=None):
        def respond(method, url, **kwargs):
            if error:
                return fake_response(400, {"error": error})
            if url.endswith("/subscribed_apps"):
                return fake_response(200, {"data": [{"whatsapp_business_api_data": {"id": app}} for app in apps]})
            return fake_response(200, number)
        with patch(REQUEST, side_effect=respond):
            self.account.action_test_connection()
        return self.account

    def test_connection_states(self):
        number = {"status": "CONNECTED", "display_phone_number": "15550783881",
                  "verified_name": "Support Number", "quality_rating": "GREEN"}
        account = self._test_connection(number, ["1234567890"])
        self.assertEqual(account.connection_state, "connected")
        self.assertEqual(account.verified_name, "Support Number")
        self.assertTrue(account.sudo().webhook_verify_token)

        self._test_connection(number, ["someone-else"])
        self.assertEqual(account.connection_state, "webhook_error")

        self._test_connection({**number, "status": "PENDING"}, ["1234567890"])
        self.assertEqual(account.connection_state, "connection_error")

        self._test_connection(number, [], error={"message": "expired", "type": "OAuthException", "code": 190})
        self.assertEqual(account.connection_state, "token_error")

    def test_template_sync(self):
        meta = [{
            "id": "920070352646140", "name": "2023_april_promo", "language": "en_US",
            "status": "REJECTED", "category": "MARKETING",
            "components": [
                {"format": "TEXT", "text": "Fall Sale", "type": "HEADER"},
                {"text": "Hi {{1}}, our Fall Sale is on!", "type": "BODY"},
                {"text": "Tap Stop Promotions", "type": "FOOTER"},
                {"buttons": [{"text": "Stop promotions", "type": "QUICK_REPLY"}], "type": "BUTTONS"},
            ],
        }]
        with patch.object(WhatsAppApi, "get_templates", return_value=meta):
            self.account.action_sync_templates()
        template = self.env["whatsapp_connector.template"].search([("template_name", "=", "2023_april_promo")])
        self.assertEqual(template.status, "REJECTED")
        self.assertEqual(template.category, "marketing")
        self.assertEqual(template.header_type, "text")
        self.assertEqual(template.header_text, "Fall Sale")
        self.assertIn("{{1}}", template.body)
        self.assertEqual(template.button_ids.text, "Stop promotions")
        meta[0]["status"] = "APPROVED"
        with patch.object(WhatsAppApi, "get_templates", return_value=meta):
            self.account.action_sync_templates()
        self.assertEqual(template.status, "APPROVED")
        self.assertEqual(len(template.button_ids), 1)
