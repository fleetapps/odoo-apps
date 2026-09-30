import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tools import mute_logger

from . import payloads
from .common import WhatsappCase
from odoo.addons.whatsapp_connector.tools.meta_api import WhatsAppApi


@tagged("post_install", "-at_install")
class TestProcessor(WhatsappCase):
    """Incoming webhooks become Discuss conversations (SPEC.md §8-§10, §22, §24, R16-R22)."""

    def _process(self, payload):
        event = self.env["whatsapp_connector.webhook.event"].create({"raw_body": json.dumps(payload)})
        event._process()
        self.assertEqual(event.state, "processed", event.error)
        return event

    def _conversations(self):
        return self.env["discuss.channel"].search([("channel_type", "=", "whatsapp")])

    def test_customer_initiated_conversation(self):
        """§8/§9: a customer's first message opens a conversation with every Notify user."""
        now = str(int(fields.Datetime.now().timestamp()))
        self._process(payloads.inbound([payloads.text_message(
            "wamid.1", "Does it come in another color?", timestamp=now,
        )]))
        channel = self._conversations()
        self.assertEqual(len(channel), 1)
        self.assertEqual(channel.wa_partner_id, self.customer)  # matched by BSUID
        self.assertEqual(channel.wa_bsuid, payloads.BSUID)
        self.assertEqual(channel.wa_customer_phone, "+16505551234")
        self.assertEqual(
            channel.channel_member_ids.partner_id,
            (self.user_a | self.user_b | self.user_c).partner_id,
            "Notify users are members; OdooBot, who ran the processing, is not",
        )
        message = channel.message_ids[:1]
        self.assertEqual(message.message_type, "whatsapp_message")
        self.assertEqual(message.author_id, self.customer)
        self.assertIn("Does it come in another color?", message.body)
        wa = self.env["whatsapp_connector.message"].search([("external_message_id", "=", "wamid.1")])
        self.assertEqual(wa.mail_message_id, message)
        self.assertEqual(wa.direction, "inbound")
        self.assertTrue(channel._wa_window_open())

    def test_returning_customer_same_conversation(self):
        """§22: a later message goes to the existing conversation, not a new one."""
        self._process(payloads.inbound([payloads.text_message("wamid.1", "Monday")]))
        self._process(payloads.inbound([payloads.text_message("wamid.2", "Thursday", timestamp="1749700000")]))
        channel = self._conversations()
        self.assertEqual(len(channel), 1)
        self.assertEqual(len(channel.message_ids.filtered(lambda m: m.message_type == "whatsapp_message")), 2)

    def test_duplicate_delivery_ignored(self):
        """§24 / R35: Meta's retries can duplicate a webhook."""
        payload = payloads.inbound([payloads.text_message("wamid.1", "Hello")])
        self._process(payload)
        self._process(payload)
        self.assertEqual(self.env["whatsapp_connector.message"].search_count(
            [("external_message_id", "=", "wamid.1")]), 1)
        self.assertEqual(len(self._conversations().message_ids.filtered(
            lambda m: m.message_type == "whatsapp_message")), 1)

    def test_bsuid_only_customer(self):
        """R16: a customer with a username and no visible phone number."""
        bsuid = "US.99990000111122223333"
        payload = payloads.inbound(
            [payloads.text_message("wamid.9", "Hi", wa_id=None, bsuid=bsuid)],
            contacts=[payloads.contact(name="Real Sheena", wa_id=None, bsuid=bsuid, username="realsheena")],
        )
        self._process(payload)
        channel = self._conversations()
        self.assertEqual(channel.wa_bsuid, bsuid)
        self.assertFalse(channel.wa_customer_phone)
        partner = channel.wa_partner_id
        self.assertEqual(partner.name, "Real Sheena")
        self.assertEqual(partner.wa_username, "realsheena")
        self.assertFalse(partner.phone)
        # the same customer again, still without a phone: same conversation
        self._process(payloads.inbound(
            [payloads.text_message("wamid.10", "Again", wa_id=None, bsuid=bsuid, timestamp="1749416999")],
            contacts=[payloads.contact(name="Real Sheena", wa_id=None, bsuid=bsuid, username="realsheena")],
        ))
        self.assertEqual(len(self._conversations()), 1)

    def test_phone_arrives_later(self):
        """R16: a phone number that arrives later fills the contact, no duplicate contact."""
        bsuid = "US.55550000111122223333"
        self._process(payloads.inbound(
            [payloads.text_message("wamid.20", "Hi", wa_id=None, bsuid=bsuid)],
            contacts=[payloads.contact(name="Kim", wa_id=None, bsuid=bsuid)],
        ))
        self._process(payloads.inbound(
            [payloads.text_message("wamid.21", "Here", wa_id="254712345678", bsuid=bsuid, timestamp="1749417000")],
            contacts=[payloads.contact(name="Kim", wa_id="254712345678", bsuid=bsuid)],
        ))
        channel = self._conversations()
        self.assertEqual(len(channel), 1)
        self.assertEqual(channel.wa_partner_id.phone, "+254712345678")
        self.assertEqual(self.env["res.partner"].search_count([("wa_bsuid", "=", bsuid)]), 1)

    def test_phone_customer_matched_to_existing_contact(self):
        """§42: no BSUID match, the normalized phone finds the contact."""
        partner = self.env["res.partner"].create({"name": "John", "phone": "+254 712 345 678"})
        self._process(payloads.inbound(
            [payloads.text_message("wamid.30", "Hi", wa_id="254712345678", bsuid="KE.1234")],
            contacts=[payloads.contact(name="Johnny", wa_id="254712345678", bsuid="KE.1234")],
        ))
        channel = self._conversations()
        self.assertEqual(channel.wa_partner_id, partner)
        self.assertEqual(partner.wa_bsuid, "KE.1234")

    def test_bsuid_change_rekeys_conversation(self):
        """R16: user_changed_user_id and user_id_update keep the same conversation."""
        self._process(payloads.inbound([payloads.text_message("wamid.1", "Hello")]))
        channel = self._conversations()
        system = {
            "from": payloads.WA_ID, "id": "wamid.sys", "timestamp": "1750269342", "type": "system",
            "system": {
                "body": "User changed from US.1349 to US.NEW1", "type": "user_changed_user_id",
                "user_id": "US.NEW1", "previous_user_id": payloads.BSUID,
            },
        }
        self._process(payloads.envelope({
            "messaging_product": "whatsapp", "metadata": payloads.metadata(), "messages": [system],
        }))
        self.assertEqual(channel.wa_bsuid, "US.NEW1")
        self.assertEqual(channel.wa_customer_key, "US.NEW1")
        self._process(payloads.envelope({
            "messaging_product": "whatsapp", "metadata": payloads.metadata(),
            "user_id_update": [{"user_id": {"previous": "US.NEW1", "current": "US.NEW2"}}],
        }, field="user_id_update"))
        self.assertEqual(channel.wa_bsuid, "US.NEW2")
        self._process(payloads.inbound([payloads.text_message(
            "wamid.2", "After", bsuid="US.NEW2", timestamp="1750300000",
        )], contacts=[payloads.contact(bsuid="US.NEW2")]))
        self.assertEqual(len(self._conversations()), 1)

    def test_batch_processed_in_timestamp_order(self):
        """R28/R35: one POST with several messages, delivered out of order."""
        late = payloads.text_message("wamid.late", "second", timestamp="1749416400")
        early = payloads.text_message("wamid.early", "first", timestamp="1749416300")
        self._process(payloads.inbound([late, early]))
        messages = self._conversations().message_ids.filtered(
            lambda m: m.message_type == "whatsapp_message").sorted("id")
        self.assertIn("first", messages[0].body)
        self.assertIn("second", messages[1].body)
        self.assertLess(messages[0].date, messages[1].date)

    def test_statuses(self):
        """R19: statuses never go backwards; failed carries Meta's error; BSUID learned."""
        channel = self._make_channel(self.user_a.partner_id, key="wa:16505551234", wa_bsuid=False)
        wa = self.env["whatsapp_connector.message"].create({
            "account_id": self.account.id, "channel_id": channel.id, "direction": "outbound",
            "status": "sent", "external_message_id": "wamid.out1", "sent_at": fields.Datetime.now(),
        })
        self._process(payloads.status("wamid.out1", "read", recipient_user_id=payloads.BSUID))
        self._process(payloads.status("wamid.out1", "delivered"))
        self.assertEqual(wa.status, "read")
        self.assertEqual(channel.wa_bsuid, payloads.BSUID)
        failing = self.env["whatsapp_connector.message"].create({
            "account_id": self.account.id, "channel_id": channel.id, "direction": "outbound",
            "status": "sent", "external_message_id": "wamid.out2",
        })
        # failed statuses come without a contacts block
        self._process(payloads.status("wamid.out2", "failed", errors=[{
            "code": 131026, "title": "Message undeliverable", "message": "Message undeliverable",
            "error_data": {"details": "Message could not be delivered"},
        }]))
        self.assertEqual(failing.status, "failed")
        self.assertEqual(failing.error_code, "131026")

    def test_reaction_and_reply(self):
        """R20: reactions become Discuss reactions; context links replies."""
        self._process(payloads.inbound([payloads.text_message("wamid.1", "Hello")]))
        channel = self._conversations()
        first = channel.message_ids[:1]
        reaction = {"from": payloads.WA_ID, "from_user_id": payloads.BSUID, "id": "wamid.r1",
                    "timestamp": "1749416500", "type": "reaction",
                    "reaction": {"message_id": "wamid.1", "emoji": "👍"}}
        self._process(payloads.inbound([reaction]))
        self.assertEqual(first.reaction_ids.content, "👍")
        self.assertEqual(first.reaction_ids.partner_id, self.customer)
        reply = payloads.text_message("wamid.2", "About that", timestamp="1749416600",
                                      context={"from": payloads.DISPLAY_NUMBER, "id": "wamid.1"})
        self._process(payloads.inbound([reply]))
        self.assertEqual(channel.message_ids[:1].parent_id, first)

    def test_other_types_rendered(self):
        """R20: location, interactive, button, order and unsupported messages are shown."""
        base = {"from": payloads.WA_ID, "from_user_id": payloads.BSUID}
        messages = [
            {**base, "id": "wamid.l", "timestamp": "1", "type": "location",
             "location": {"latitude": 37.44, "longitude": -122.16, "name": "Philz Coffee"}},
            {**base, "id": "wamid.i", "timestamp": "2", "type": "interactive",
             "interactive": {"type": "button_reply", "button_reply": {"id": "cancel", "title": "Cancel"}}},
            {**base, "id": "wamid.b", "timestamp": "3", "type": "button",
             "button": {"payload": "Unsubscribe", "text": "Unsubscribe"}},
            {**base, "id": "wamid.u", "timestamp": "4", "type": "unsupported",
             "errors": [{"code": 131051, "title": "Message type unknown"}]},
        ]
        self._process(payloads.inbound(messages))
        bodies = " ".join(self._conversations().message_ids.mapped("body"))
        for text in ("Philz Coffee", "maps.google.com", "Cancel", "Unsubscribe", "Message type unknown"):
            self.assertIn(text, bodies)

    def test_media_downloaded_and_checked(self):
        """R22: two-step download with the token; Meta's sha256 is checked."""
        content = b"\x89PNG fake image"
        sha = base64.b64encode(hashlib.sha256(content).digest()).decode()
        image = {"from": payloads.WA_ID, "from_user_id": payloads.BSUID, "id": "wamid.img",
                 "timestamp": "1749416383", "type": "image",
                 "image": {"caption": "Taj Mahal", "mime_type": "image/jpeg", "sha256": sha, "id": "MEDIA1"}}
        with patch.object(WhatsAppApi, "get_media_url", return_value={"url": "https://lookaside/x", "mime_type": "image/jpeg"}) as get_url, \
                patch.object(WhatsAppApi, "download_media", return_value=(content, "image/jpeg")):
            self._process(payloads.inbound([image]))
        get_url.assert_called_once_with("MEDIA1")
        message = self._conversations().message_ids[:1]
        self.assertEqual(message.attachment_ids.raw, content)
        self.assertEqual(message.attachment_ids.mimetype, "image/jpeg")
        self.assertIn("Taj Mahal", message.body)

    def test_media_checksum_mismatch_not_attached(self):
        image = {"from": payloads.WA_ID, "from_user_id": payloads.BSUID, "id": "wamid.img2",
                 "timestamp": "1749416383", "type": "image",
                 "image": {"mime_type": "image/jpeg", "sha256": "bm90LXRoZS1zYW1l", "id": "MEDIA2"}}
        with patch.object(WhatsAppApi, "get_media_url", return_value={"url": "https://lookaside/y"}), \
                patch.object(WhatsAppApi, "download_media", return_value=(b"tampered", "image/jpeg")):
            self._process(payloads.inbound([image]))
        wa = self.env["whatsapp_connector.message"].search([("external_message_id", "=", "wamid.img2")])
        self.assertEqual(wa.media_download_state, "failed")
        self.assertFalse(wa.mail_message_id.attachment_ids)

    @mute_logger("odoo.addons.whatsapp_connector.models.whatsapp_webhook_event")
    def test_unknown_number_is_an_error(self):
        event = self.env["whatsapp_connector.webhook.event"].create({"raw_body": json.dumps(
            payloads.inbound([payloads.text_message("wamid.x", "Hi")], phone_number_id="999"),
        )})
        event._process()
        self.assertEqual(event.state, "error")
        self.assertIn("999", event.error)

    def test_notify_rule_after_15_days(self):
        """R6: after 15 days without a reply, listeners are notified again."""
        self._process(payloads.inbound([payloads.text_message("wamid.1", "Hello")]))
        channel = self._conversations()
        member_b = channel.channel_member_ids.filtered(lambda m: m.partner_id == self.user_b.partner_id)
        member_b.write({"mute_until_dt": fields.Datetime.now() + timedelta(days=15)})
        channel.wa_last_user_message_at = fields.Datetime.now() - timedelta(days=16)
        self._process(payloads.inbound([payloads.text_message(
            "wamid.2", "Anyone?", timestamp=str(int(fields.Datetime.now().timestamp())),
        )]))
        self.assertFalse(member_b.mute_until_dt)
