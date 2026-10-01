from datetime import timedelta

from odoo import Command, fields
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tests.common import JsonRpcException
from odoo.tools import mute_logger

from .test_outbound import PHONE, OutboundCase, sent
from odoo.addons.mail.tools.discuss import Store


@tagged("post_install", "-at_install")
class TestStoreData(OutboundCase):
    """What the Discuss UI receives (SPEC.md §19, §28, §35)."""

    def test_conversation_fields(self):
        channel = self._open_channel()
        data = Store().add(channel.with_user(self.user_a)).get_result()["discuss.channel"][0]
        self.assertEqual(data["wa_customer_phone"], PHONE)
        self.assertEqual(data["wa_status"], "open")
        self.assertTrue(data["wa_window_expires_at"])
        self.assertIs(data["wa_lead_id"], False)
        # the customer's picture (C), and no Discuss "seen" ticks next to WhatsApp's (A)
        self.assertEqual(data["wa_partner_id"], self.customer.id)
        result = Store().add(channel.with_user(self.user_a)).get_result()
        partner = next(p for p in result["res.partner"] if p["id"] == self.customer.id)
        self.assertIn("avatar_128_access_token", partner)
        self.assertNotIn("whatsapp", self.env["discuss.channel"]._types_allowing_seen_infos())
        self.assertIs(data["wa_can_create_lead"], True)
        no_sales = new_test_user(
            self.env, login="wa_nosales", groups="base.group_user,whatsapp_connector.group_whatsapp_user",
        )
        channel.channel_member_ids = [Command.create({"partner_id": no_sales.partner_id.id})]
        no_sales_data = Store().add(channel.with_user(no_sales)).get_result()["discuss.channel"][0]
        self.assertIs(no_sales_data["wa_can_create_lead"], False)
        group = self.env["discuss.channel"].create({"name": "Team", "channel_type": "group"})
        self.assertNotIn("wa_customer_phone", Store().add(group).get_result()["discuss.channel"][0])

    def test_delivery_status(self):
        channel = self._open_channel()
        photo = self._attachment(self.user_a, "a.png", "image/png")
        message = self._post(channel, self.user_a, "Here", attachment_ids=[photo.id])
        self.assertEqual(message._wa_delivery(), ("queued", False))
        image, = message.wa_message_ids
        image.status = "read"
        self.assertEqual(message._wa_delivery(), ("read", False))
        self._post(channel, self.user_a, "Hello")  # another message, not counted
        image.write({"status": "failed", "error_title": "Media upload error"})
        self.assertEqual(message._wa_delivery(), ("failed", "Media upload error"))
        data = Store().add(message).get_result()["mail.message"][0]
        self.assertEqual((data["whatsappStatus"], data["whatsappError"]), ("failed", "Media upload error"))
        incoming = channel.with_context(wa_skip_send=True).message_post(
            body="Hi", author_id=self.customer.id, message_type="whatsapp_message",
        )
        self.assertEqual(incoming._wa_delivery(), (False, False))


@tagged("post_install", "-at_install")
class TestUi(OutboundCase, HttpCase):
    """The Discuss and chatter UI, in a browser (SPEC.md §19, §28, §35, R5, R10)."""

    def _discuss(self, channel):
        return f"/odoo/discuss?active_id=discuss.channel_{channel.id}"

    def test_reply_from_discuss(self):
        channel = self._open_channel()
        self.start_tour(self._discuss(channel), "whatsapp_connector_discuss_reply", login="wa_andrew")
        wa = channel.wa_message_ids
        self.assertRecordValues(wa, [{"body": "Hello from Discuss", "status": "queued"}])

    def test_closed_window_sends_template(self):
        channel = self._open_channel(last_customer_message=fields.Datetime.now() - timedelta(days=2))
        self.env["whatsapp_connector.template"].create({
            "name": "Reconnect", "template_name": "reconnect", "account_id": self.account.id,
            "status": "APPROVED", "model_id": self.env["ir.model"]._get_id("res.partner"),
            "body": "Hi {{1}}, can we continue?",
            "variable_ids": [Command.create({"line_type": "body", "placeholder_index": 1,
                                             "field_type": "field", "field_name": "name"})],
        })
        self.start_tour(self._discuss(channel), "whatsapp_connector_discuss_locked", login="wa_andrew")
        self.assertEqual(channel.wa_message_ids.message_type, "template")

    def test_chatter_button(self):
        self.env["whatsapp_connector.template"].create({
            "name": "Quote follow-up", "template_name": "quote_followup", "account_id": self.account.id,
            "status": "APPROVED", "model_id": self.env["ir.model"]._get_id("crm.lead"),
            "body": "Hello {{1}}",
            "variable_ids": [Command.create({"line_type": "body", "placeholder_index": 1,
                                             "field_type": "field", "field_name": "partner_id.name"})],
        })
        lead = self.env["crm.lead"].create({"name": "Sofa", "partner_id": self.customer.id, "user_id": self.user_a.id})
        self.start_tour(f"/odoo/crm.lead/{lead.id}", "whatsapp_connector_chatter", login="wa_andrew")
        wa = self.env["whatsapp_connector.message"].search([("template_id.template_name", "=", "quote_followup")])
        self.assertEqual(wa.channel_id.wa_partner_id, self.customer)

    def test_create_lead_from_discuss(self):
        """[D8] Create Lead is also a Discuss action on the conversation."""
        channel = self._open_channel()
        self.start_tour(self._discuss(channel), "whatsapp_connector_create_lead", login="wa_andrew")
        self.assertEqual(channel.wa_lead_id.name, "WhatsApp — Sheena Nelson")

    def test_retry(self):
        channel = self._open_channel()
        # right after another reply of the same user: Discuss would squash it
        self._post(channel, self.user_a, "Arrived fine").wa_message_ids.status = "delivered"
        message = self._post(channel, self.user_a, "Did not arrive")
        message.wa_message_ids.write({"status": "failed", "error_title": "Re-engagement message"})
        self.start_tour(self._discuss(channel), "whatsapp_connector_retry", login="wa_andrew")
        self.assertEqual(message.wa_message_ids.status, "queued")
        self._send_queued(answer=sent("wamid.again"))
        self.assertEqual(message.wa_message_ids.external_message_id, "wamid.again")

    @mute_logger("odoo.http")
    def test_retry_requires_access(self):
        channel = self._open_channel()
        message = self._post(channel, self.user_a, "Private")
        message.wa_message_ids.status = "failed"
        self._member(channel, self.user_c).sudo().unlink()
        self.authenticate("wa_brian", "wa_brian")
        with self.assertRaises(JsonRpcException):
            self.make_jsonrpc_request("/whatsapp_connector/message/retry", {"message_id": message.id})
        self.assertEqual(message.wa_message_ids.status, "failed")
