import base64
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tests.common import JsonRpcException
from odoo.tools import mute_logger

from .common import WhatsappCase
from odoo.addons.whatsapp_connector.tools.meta_api import MetaApiError, WhatsAppApi

PHONE = "+16505551234"
BSUID = "US.13491208655302741918"
# smallest valid PNG
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
)


def sent(msg_id="wamid.out1", wa_id="16505551234", **contact):
    """Meta's answer to a send request."""
    return {
        "messaging_product": "whatsapp",
        "contacts": [{"input": PHONE, "wa_id": wa_id, **contact}],
        "messages": [{"id": msg_id}],
    }


class OutboundCase(WhatsappCase):

    def _open_channel(self, last_customer_message=None, **extra):
        """A conversation of the three Notify users with a customer who wrote just now."""
        values = {
            "wa_customer_phone": PHONE,
            "wa_id": "16505551234",
            "wa_last_customer_message_at": last_customer_message or fields.Datetime.now(),
        }
        values.update(extra)
        channel = self._make_channel((self.user_a | self.user_b | self.user_c).partner_id, **values)
        channel.channel_member_ids.write({"wa_participant": False})
        return channel

    def _member(self, channel, user):
        return channel.channel_member_ids.filtered(lambda m: m.partner_id == user.partner_id)

    def _send_queued(self, answer=None, side_effect=None):
        """Run the sending cron with Meta mocked; return the mocked send_message."""
        with patch.object(WhatsAppApi, "send_message", return_value=answer or sent(),
                          side_effect=side_effect) as send:
            self.env["whatsapp_connector.message"]._cron_send_queued()
        return send

    def _attachment(self, user, name, mimetype, raw=PNG):
        # what /mail/attachment/upload creates: pending on the composer
        return self.env["ir.attachment"].with_user(user).create({
            "name": name, "raw": raw, "mimetype": mimetype,
            "res_model": "mail.compose.message", "res_id": 0,
        })

    def _post(self, channel, user, body="", **kwargs):
        return channel.with_user(user).message_post(
            body=body, message_type="comment", subtype_xmlid="mail.mt_comment", **kwargs,
        )


@tagged("post_install", "-at_install")
class TestOutbound(OutboundCase):
    """Replies from Discuss are sent through Meta (SPEC.md §27, §28, §35, R6, R19, R22, R34, R36)."""

    def test_reply_queued_then_sent(self):
        """§27: a Discuss reply becomes a WhatsApp message, queued then sent to the E.164 number."""
        channel = self._open_channel()
        message = self._post(channel, self.user_a, "Yes, it comes in blue: https://shop.example.com/blue")
        self.assertEqual(message.message_type, "whatsapp_message")
        wa = message.wa_message_ids
        self.assertEqual(len(wa), 1)
        self.assertRecordValues(wa, [{
            "direction": "outbound", "status": "queued", "message_type": "text",
            "recipient": PHONE, "account_id": self.account.id, "channel_id": channel.id,
        }])
        # links keep their text, without numbered footnotes
        self.assertEqual(wa.body, "Yes, it comes in blue: https://shop.example.com/blue")
        cron = self.env.ref("whatsapp_connector.ir_cron_send_messages")
        self.assertTrue(self.env["ir.cron.trigger"].search([("cron_id", "=", cron.id)]))

        send = self._send_queued()
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], {
            "to": PHONE, "type": "text",
            "text": {"body": "Yes, it comes in blue: https://shop.example.com/blue", "preview_url": True},
        })
        # accepted by Meta; "sent" comes with the status webhook (R19)
        self.assertRecordValues(wa, [{"external_message_id": "wamid.out1", "status": "queued"}])
        self.assertTrue(wa.sent_at)
        self.assertFalse(self._send_queued().called, "a sent message is never sent twice")

    def test_reply_to_a_customer_message(self):
        """A reply in Discuss is a reply in WhatsApp: context.message_id."""
        channel = self._open_channel()
        incoming = channel.with_context(wa_skip_send=True).message_post(
            body="Which sizes?", author_id=self.customer.id, message_type="whatsapp_message",
        )
        self.env["whatsapp_connector.message"].create({
            "account_id": self.account.id, "channel_id": channel.id, "mail_message_id": incoming.id,
            "external_message_id": "wamid.in1", "direction": "inbound", "status": "received",
        })
        self.assertFalse(incoming.wa_message_ids.filtered(lambda m: m.direction == "outbound"))
        reply = self._post(channel, self.user_a, "S, M and L", parent_id=incoming.id)
        self.assertEqual(reply.wa_message_ids.context_message_id, "wamid.in1")
        send = self._send_queued()
        self.assertEqual(send.call_args.args[0]["context"], {"message_id": "wamid.in1"})

    def test_media_with_caption(self):
        """R22: files are uploaded to Meta and sent by ID; the text is the first caption."""
        channel = self._open_channel()
        photo = self._attachment(self.user_a, "blue.png", "image/png")
        brochure = self._attachment(self.user_a, "brochure.pdf", "application/pdf", raw=b"%PDF-1.4 test")
        message = self._post(channel, self.user_a, "Here it is", attachment_ids=[photo.id, brochure.id])
        self.assertEqual(message.attachment_ids, photo | brochure)
        wa = message.wa_message_ids.sorted("id")
        self.assertEqual(wa.mapped("message_type"), ["image", "document"])
        self.assertEqual(wa.mapped("body"), ["Here it is", False])
        with patch.object(WhatsAppApi, "upload_media", side_effect=["media.1", "media.2"]) as upload:
            send = self._send_queued(side_effect=[sent("wamid.img"), sent("wamid.doc")])
        self.assertEqual(upload.call_args_list[0].args, ("blue.png", "image/png", PNG))
        self.assertEqual([c.args[0] for c in send.call_args_list], [
            {"to": PHONE, "type": "image", "image": {"id": "media.1", "caption": "Here it is"}},
            {"to": PHONE, "type": "document", "document": {"id": "media.2", "filename": "brochure.pdf"}},
        ])

    def test_audio_text_sent_separately(self):
        """Audio has no caption in WhatsApp: the text goes as its own message."""
        channel = self._open_channel()
        voice = self._attachment(self.user_a, "note.ogg", "audio/ogg", raw=b"OggS")
        message = self._post(channel, self.user_a, "Listen", attachment_ids=[voice.id])
        wa = message.wa_message_ids.sorted("id")
        self.assertEqual(wa.mapped("message_type"), ["audio", "text"])
        self.assertEqual(wa.mapped("body"), [False, "Listen"])

    def test_refused_outside_customer_service_window(self):
        """R34: after 24 hours only templates can be sent; nothing is posted."""
        channel = self._open_channel(last_customer_message=fields.Datetime.now() - timedelta(hours=25))
        count = len(channel.message_ids)
        with self.assertRaisesRegex(UserError, "24 hours"):
            self._post(channel, self.user_a, "Still there?")
        self.assertEqual(len(channel.message_ids), count)

    def test_refused_files_meta_does_not_accept(self):
        """R22: unsupported types and files over Meta's limit are refused before posting."""
        channel = self._open_channel()
        archive = self._attachment(self.user_a, "photos.zip", "application/zip", raw=b"PK")
        with self.assertRaisesRegex(UserError, "cannot send photos.zip"):
            self._post(channel, self.user_a, "", attachment_ids=[archive.id])
        big = self._attachment(self.user_a, "big.png", "image/png", raw=b"0" * (5 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(UserError, "too large"):
            self._post(channel, self.user_a, "", attachment_ids=[big.id])
        with self.assertRaisesRegex(UserError, "4096"):
            self._post(channel, self.user_a, "x" * 4097)

    def test_bsuid_recipient(self):
        """R36: without a phone number, the BSUID is used only once the account enables it."""
        channel = self._open_channel(wa_customer_phone=False, wa_id=False)
        with self.assertRaisesRegex(UserError, "no phone number"):
            self._post(channel, self.user_a, "Hello")
        self.account.bsuid_sending_enabled = True
        self._post(channel, self.user_a, "Hello")
        send = self._send_queued(answer=sent(wa_id=None, user_id=BSUID))
        payload = send.call_args.args[0]
        self.assertEqual(payload["recipient"], BSUID)
        self.assertNotIn("to", payload)

    def test_meta_error_then_retry(self):
        """§35: Meta's refusal marks the message failed with its reason; Retry sends it again."""
        channel = self._open_channel()
        wa = self._post(channel, self.user_a, "Hello").wa_message_ids
        error = MetaApiError(
            "Message failed to send because more than 24 hours have passed",
            code=131047, title="Re-engagement message", details="More than 24 hours have passed.",
        )
        self._send_queued(side_effect=error)
        self.assertRecordValues(wa, [{
            "status": "failed", "error_code": "131047", "error_title": "Re-engagement message",
            "error_details": "More than 24 hours have passed.", "external_message_id": False,
        }])
        self.assertEqual(self.account.connection_state, "not_tested", "not an account problem")
        wa.with_user(self.user_a).action_retry()
        self.assertRecordValues(wa, [{"status": "queued", "error_code": False, "error_title": False}])
        self._send_queued(answer=sent("wamid.retry"))
        self.assertEqual(wa.external_message_id, "wamid.retry")

    def test_retry_refused_once_window_closed(self):
        channel = self._open_channel()
        wa = self._post(channel, self.user_a, "Hello").wa_message_ids
        wa.sudo().status = "failed"
        channel.wa_last_customer_message_at = fields.Datetime.now() - timedelta(days=2)
        with self.assertRaisesRegex(UserError, "only a template"):
            wa.with_user(self.user_a).action_retry()

    def test_answer_without_id_is_not_resent(self):
        channel = self._open_channel()
        wa = self._post(channel, self.user_a, "Hello").wa_message_ids
        self._send_queued(answer={"messaging_product": "whatsapp", "messages": []})
        self.assertEqual(wa.status, "failed")
        self.assertFalse(self._send_queued().called)

    def test_token_error_flags_the_account(self):
        """Error 190: the token is invalid; the account shows Token Error (R24)."""
        channel = self._open_channel()
        wa = self._post(channel, self.user_a, "Hello").wa_message_ids
        self._send_queued(side_effect=MetaApiError("Error validating access token", code=190))
        self.assertEqual(wa.status, "failed")
        self.assertEqual(self.account.connection_state, "token_error")

    @mute_logger("odoo.addons.whatsapp_connector.models.whatsapp_message")
    def test_unexpected_error_fails_only_that_message(self):
        channel = self._open_channel()
        first = self._post(channel, self.user_a, "One").wa_message_ids
        second = self._post(channel, self.user_a, "Two").wa_message_ids
        self._send_queued(side_effect=[RuntimeError("disk full"), sent("wamid.two")])
        self.assertEqual(first.status, "failed")
        self.assertIn("disk full", first.error_message)
        self.assertEqual(second.external_message_id, "wamid.two")

    def test_reply_mutes_listeners_for_15_days(self):
        """R6: the user who replies takes part; the other Notify users are muted for 15 days."""
        channel = self._open_channel()
        self._post(channel, self.user_a, "I'll take this one")
        member_a, member_b = self._member(channel, self.user_a), self._member(channel, self.user_b)
        self.assertTrue(member_a.wa_participant)
        self.assertFalse(member_a.mute_until_dt)
        self.assertFalse(member_b.wa_participant)
        self.assertAlmostEqual(
            member_b.mute_until_dt, fields.Datetime.now() + timedelta(days=15), delta=timedelta(minutes=1),
        )
        self.assertTrue(channel.wa_last_user_message_at)
        # a customer message within 15 days does not unmute them
        channel._wa_apply_notify_rule(fields.Datetime.now())
        self.assertTrue(member_b.mute_until_dt)

    def test_reply_reopens_closed_conversation(self):
        """§44: a closed conversation reopens when a user writes in it, not when the send is refused."""
        channel = self._open_channel(wa_status="closed")
        self._post(channel, self.user_a, "We're back")
        self.assertEqual(channel.wa_status, "open")
        late = self._open_channel(
            key="US.0000", wa_status="closed",
            last_customer_message=fields.Datetime.now() - timedelta(days=2),
        )
        with self.assertRaises(UserError):
            self._post(late, self.user_a, "Hello?")
        self.assertEqual(late.wa_status, "closed")

    def test_editing_a_sent_message_is_refused(self):
        """Core Discuss only edits comments: what the customer received cannot be changed."""
        channel = self._open_channel()
        message = self._post(channel, self.user_a, "Hello")
        with self.assertRaises(UserError):
            channel._message_update_content(message, body="Goodbye")

    def test_message_rules(self):
        """§31 / R30: WhatsApp message data is readable by the conversation's members and managers."""
        channel = self._open_channel()
        wa = self._post(channel, self.user_a, "Hello").wa_message_ids
        Message = self.env["whatsapp_connector.message"]
        self.assertEqual(Message.with_user(self.user_b).search([("id", "=", wa.id)]), wa)
        self.assertEqual(Message.with_user(self.manager).search([("id", "=", wa.id)]), wa)
        self._member(channel, self.user_c).sudo().unlink()
        self.assertFalse(Message.with_user(self.user_c).search([("id", "=", wa.id)]))


@tagged("post_install", "-at_install")
class TestDroppedMessages(OutboundCase):

    def test_dropped_after_time_to_live(self):
        """R19: without "delivered" within Meta's time-to-live, the message is dropped."""
        channel = self._open_channel()
        auth = self.env["whatsapp_connector.template"].create({
            "name": "Code", "template_name": "login_code", "account_id": self.account.id,
            "category": "authentication",
        })
        Message = self.env["whatsapp_connector.message"]
        base = {"account_id": self.account.id, "channel_id": channel.id, "direction": "outbound"}
        now = fields.Datetime.now()
        old = Message.create({**base, "external_message_id": "w1", "status": "sent",
                              "sent_at": now - timedelta(days=31)})
        recent = Message.create({**base, "external_message_id": "w2", "status": "sent",
                                 "sent_at": now - timedelta(days=29)})
        code = Message.create({**base, "external_message_id": "w3", "status": "queued",
                               "message_type": "template", "template_id": auth.id,
                               "sent_at": now - timedelta(minutes=11)})
        delivered = Message.create({**base, "external_message_id": "w4", "status": "delivered",
                                    "sent_at": now - timedelta(days=40)})
        Message._cron_mark_dropped()
        self.assertEqual((old | recent | code | delivered).mapped("status"),
                         ["dropped", "sent", "dropped", "delivered"])


@tagged("post_install", "-at_install")
class TestTemplateSend(OutboundCase):
    """A template sent from a record starts or reuses the conversation (SPEC.md §8.1, R5, R25)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.env["whatsapp_connector.template"].create({
            "name": "Order ready",
            "template_name": "order_ready",
            "language_code": "en_US",
            "account_id": cls.account.id,
            "status": "APPROVED",
            "model_id": cls.env["ir.model"]._get_id("crm.lead"),
            "phone_field": "partner_id",
            "header_type": "text",
            "header_text": "Update for {{1}}",
            "body": "Hello {{1}}, your order {{2}} is ready.",
            "footer": "Reply STOP to opt out",
            "variable_ids": [
                Command.create({"line_type": "header", "placeholder_index": 1,
                                "field_type": "field", "field_name": "partner_id.name"}),
                Command.create({"line_type": "body", "placeholder_index": 1,
                                "field_type": "field", "field_name": "partner_id.name"}),
                Command.create({"line_type": "body", "placeholder_index": 2,
                                "field_type": "field", "field_name": "name"}),
            ],
        })
        cls.lead = cls.env["crm.lead"].create({
            "name": "Order 42", "partner_id": cls.customer.id, "user_id": cls.user_b.id,
        })

    def test_template_starts_conversation(self):
        wa = self.template.with_user(self.user_b)._wa_send_to_record(self.lead)
        channel = wa.channel_id
        self.assertRecordValues(channel, [{
            "channel_type": "whatsapp", "wa_partner_id": self.customer.id, "wa_customer_phone": PHONE,
            "wa_bsuid": BSUID, "wa_status": "open",
        }])
        # Mode A: the sender is the member, so the customer's answer pops up for them
        self.assertEqual(channel.channel_member_ids.partner_id, self.user_b.partner_id)
        self.assertTrue(self._member(channel, self.user_b).wa_participant)
        message = wa.mail_message_id
        self.assertRecordValues(message, [{"message_type": "whatsapp_message",
                                           "author_id": self.user_b.partner_id.id}])
        self.assertIn("Hello Sheena Nelson, your order Order 42 is ready.", message.body)
        self.assertIn("Update for Sheena Nelson", message.body)
        self.assertEqual(len(message.wa_message_ids), 1, "posted once, not sent as a text too")
        note = self.lead.message_ids.filtered(lambda m: "Order ready" in (m.body or ""))
        self.assertTrue(note, "the record's chatter links to the conversation")

        send = self._send_queued()
        self.assertEqual(send.call_args.args[0], {
            "to": PHONE,
            "type": "template",
            "template": {
                "name": "order_ready",
                "language": {"policy": "deterministic", "code": "en_US"},
                "components": [
                    {"type": "header", "parameters": [{"type": "text", "text": "Sheena Nelson"}]},
                    {"type": "body", "parameters": [
                        {"type": "text", "text": "Sheena Nelson"}, {"type": "text", "text": "Order 42"},
                    ]},
                ],
            },
        })

    def test_template_reuses_open_conversation(self):
        """The customer's open conversation is reused; the sender joins and takes part."""
        channel = self._open_channel()
        self._member(channel, self.user_b).sudo().unlink()
        wa = self.template.with_user(self.user_b)._wa_send_to_record(self.lead)
        self.assertEqual(wa.channel_id, channel)
        self.assertTrue(self._member(channel, self.user_b).wa_participant)
        self.assertTrue(self._member(channel, self.user_a).mute_until_dt, "the listeners are muted")

    def test_template_works_outside_the_window(self):
        channel = self._open_channel(last_customer_message=fields.Datetime.now() - timedelta(days=3))
        wa = self.template.with_user(self.user_b)._wa_send_to_record(self.lead)
        self.assertEqual(wa.channel_id, channel)
        self.assertEqual(wa.status, "queued")

    def test_header_media_uploaded_when_sending(self):
        header = self.env["ir.attachment"].create({"name": "order.png", "raw": PNG, "mimetype": "image/png"})
        self.template.write({"header_type": "image", "header_attachment_id": header.id, "header_text": False})
        self.template.variable_ids.filtered(lambda v: v.line_type == "header").unlink()
        self.template.with_user(self.user_b)._wa_send_to_record(self.lead)
        with patch.object(WhatsAppApi, "upload_media", return_value="media.h") as upload:
            send = self._send_queued()
        upload.assert_called_once()
        components = send.call_args.args[0]["template"]["components"]
        self.assertEqual(components[0], {"type": "header", "parameters": [
            {"type": "image", "image": {"id": "media.h"}},
        ]})

    def test_refusals(self):
        self.template.status = "PENDING"
        with self.assertRaisesRegex(UserError, "not approved"):
            self.template.with_user(self.user_b)._wa_send_to_record(self.lead)
        self.template.write({"status": "APPROVED", "user_ids": [Command.set(self.user_a.ids)]})
        with self.assertRaisesRegex(UserError, "not allowed"):
            self.template.with_user(self.user_b)._wa_send_to_record(self.lead)


@tagged("post_install", "-at_install")
class TestCreateLead(OutboundCase):
    """[D8] Create lead from a Mode A conversation (SPEC.md §13)."""

    def test_creates_lead_then_opens_it(self):
        channel = self._open_channel()
        action = channel.with_user(self.user_a).action_wa_create_lead()
        lead = channel.wa_lead_id
        self.assertRecordValues(lead, [{
            "name": "WhatsApp — Sheena Nelson", "partner_id": self.customer.id,
            "user_id": self.user_a.id, "wa_bsuid": BSUID,
            "source_id": self.env.ref("whatsapp_connector.utm_source_whatsapp").id,
        }])
        self.assertEqual(lead.phone_sanitized, PHONE)
        self.assertEqual((action["res_model"], action["res_id"]), ("crm.lead", lead.id))
        notes = channel.message_ids.filtered(lambda m: m.message_type == "notification")
        self.assertEqual(len(notes), 1)
        self.assertRegex(notes.body, rf"data-oe-model=.crm\.lead. data-oe-id=.{lead.id}.", "links to the lead")
        self.assertFalse(notes.wa_message_ids, "the note is not sent to the customer")
        # a second click opens the same lead, without a new note
        channel.with_user(self.user_a).action_wa_create_lead()
        self.assertEqual(self.env["crm.lead"].search_count([("wa_bsuid", "=", BSUID)]), 1)
        self.assertEqual(len(channel.message_ids.filtered(lambda m: m.message_type == "notification")), 1)

    def test_links_existing_lead(self):
        """§13: no duplicate CRM records: the customer's open lead is linked."""
        channel = self._open_channel()
        existing = self.env["crm.lead"].create({
            "name": "Blue sofa", "phone": PHONE, "user_id": self.user_a.id,
        })
        channel.with_user(self.user_a).action_wa_create_lead()
        self.assertEqual(channel.wa_lead_id, existing)

    def test_lead_of_another_salesperson(self):
        """The lead exists but belongs to someone else: refused, never duplicated."""
        channel = self._open_channel()
        self.env["crm.lead"].create({"name": "Blue sofa", "phone": PHONE, "user_id": self.user_b.id})
        with self.assertRaisesRegex(UserError, "Sarah"):
            channel.with_user(self.user_a).action_wa_create_lead()
        self.assertFalse(channel.wa_lead_id)

    def test_lost_lead_not_reused(self):
        channel = self._open_channel()
        lost = self.env["crm.lead"].create({"name": "Old", "phone": PHONE, "user_id": self.user_a.id})
        lost.action_set_lost()
        channel.with_user(self.user_a).action_wa_create_lead()
        self.assertNotEqual(channel.wa_lead_id, lost)

    def test_requires_sales_rights(self):
        channel = self._open_channel()
        no_sales = new_test_user(
            self.env, login="wa_nosales", groups="base.group_user,whatsapp_connector.group_whatsapp_user",
        )
        channel.channel_member_ids = [Command.create({"partner_id": no_sales.partner_id.id})]
        with self.assertRaises(AccessError):
            channel.with_user(no_sales).action_wa_create_lead()


@tagged("post_install", "-at_install")
class TestDiscussComposer(OutboundCase, HttpCase):
    """The real Discuss route (/mail/message/post) sends to WhatsApp."""

    def test_post_from_discuss(self):
        channel = self._open_channel()
        self.authenticate("wa_andrew", "wa_andrew")
        result = self.make_jsonrpc_request("/mail/message/post", {
            "thread_model": "discuss.channel",
            "thread_id": channel.id,
            "post_data": {"body": "Hello from Discuss", "message_type": "comment",
                          "subtype_xmlid": "mail.mt_comment"},
        })
        message = self.env["mail.message"].browse(result["message_id"])
        self.assertEqual(message.message_type, "whatsapp_message")
        self.assertEqual(message.author_id, self.user_a.partner_id)
        self.assertRecordValues(message.wa_message_ids, [{"status": "queued", "body": "Hello from Discuss"}])

    @mute_logger("odoo.http")
    def test_post_from_discuss_outside_window(self):
        channel = self._open_channel(last_customer_message=fields.Datetime.now() - timedelta(days=2))
        self.authenticate("wa_andrew", "wa_andrew")
        with self.assertRaisesRegex(JsonRpcException, "UserError"):
            self.make_jsonrpc_request("/mail/message/post", {
                "thread_model": "discuss.channel",
                "thread_id": channel.id,
                "post_data": {"body": "Hello", "message_type": "comment"},
            })
