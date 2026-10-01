import logging
from datetime import datetime, timedelta, timezone

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools import html2plaintext, plaintext2html

from odoo.addons.mail.tools.discuss import Store

_logger = logging.getLogger(__name__)

# Meta's customer service window (SPEC.md §28, R34): free-form messages only
# within 24 hours of the customer's last message or call.
CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)
# Odoo's documented 15-day rule (SPEC.md §10, R6).
NOTIFY_ALL_AFTER = timedelta(days=15)

MEDIA_TYPES = ("image", "video", "audio", "document", "sticker")
# Meta's limit for a text message body (Cloud API reference, TextMessage.body).
TEXT_MAX_LENGTH = 4096


def is_whatsapp_channel(channel):
    return channel.channel_type == "whatsapp"


class DiscussChannel(models.Model):
    """A WhatsApp conversation is a Discuss channel of type 'whatsapp' (SPEC.md §12.1).

    The type key is the one Odoo Enterprise's WhatsApp app uses, which Odoo 19
    Community core already handles (R9). 'cascade' on uninstall is what
    im_livechat does for its own type; the alternative is open decision D5.
    """

    _inherit = "discuss.channel"

    channel_type = fields.Selection(
        selection_add=[("whatsapp", "WhatsApp Conversation")],
        ondelete={"whatsapp": "cascade"},
    )
    wa_account_id = fields.Many2one(
        # restrict: an account with conversations is archived, never deleted
        "whatsapp_connector.account", "WhatsApp Account", index="btree_not_null", ondelete="restrict",
    )
    # Identity (R16): the business-scoped user ID first; wa_customer_key is the
    # BSUID when known, otherwise "wa:<WhatsApp ID>" for conversations from
    # before BSUIDs. One open conversation per account and key.
    wa_customer_key = fields.Char(index="btree_not_null", copy=False)
    wa_bsuid = fields.Char("Business-scoped User ID", index="btree_not_null", copy=False)
    wa_parent_bsuid = fields.Char("Parent BSUID", copy=False)
    wa_id = fields.Char("WhatsApp ID", index="btree_not_null", copy=False)
    wa_customer_phone = fields.Char("Customer Phone", copy=False, help="E.164, when WhatsApp provides it.")
    wa_username = fields.Char("WhatsApp Username", copy=False)
    wa_partner_id = fields.Many2one("res.partner", "Customer", index="btree_not_null", ondelete="set null")
    wa_lead_id = fields.Many2one("crm.lead", "Lead", index="btree_not_null", ondelete="set null")
    wa_assigned_user_id = fields.Many2one("res.users", "Assigned to", index="btree_not_null", ondelete="set null")
    wa_assigned_team_id = fields.Many2one("crm.team", "Sales Team", ondelete="set null")
    wa_status = fields.Selection(
        [("open", "Open"), ("closed", "Closed")], "Conversation Status", default="open", copy=False,
    )
    wa_last_message_at = fields.Datetime("Last Message", copy=False)
    wa_last_customer_message_at = fields.Datetime("Last Customer Message", copy=False)
    wa_last_user_message_at = fields.Datetime("Last Reply", copy=False)
    wa_window_expires_at = fields.Datetime(
        "Free-form Messages Until", compute="_compute_wa_window_expires_at",
        help="End of Meta's 24-hour customer service window. After it, only templates can be sent.",
    )
    wa_referral = fields.Json("Ad Referral", copy=False, help="Click-to-WhatsApp ad data, when present.")
    wa_message_ids = fields.One2many("whatsapp_connector.message", "channel_id", string="WhatsApp Messages")

    _wa_open_conversation_unique = models.UniqueIndex(
        "(wa_account_id, wa_customer_key) WHERE channel_type = 'whatsapp' AND wa_status = 'open'",
        "This customer already has an open WhatsApp conversation on this account.",
    )

    @api.depends("wa_last_customer_message_at")
    def _compute_wa_window_expires_at(self):
        for channel in self:
            last = channel.wa_last_customer_message_at
            channel.wa_window_expires_at = last + CUSTOMER_SERVICE_WINDOW if last else False

    def _wa_window_open(self):
        self.ensure_one()
        return bool(self.wa_window_expires_at and self.wa_window_expires_at > fields.Datetime.now())

    def _wa_store_fields(self):
        """What the Discuss UI shows of a WhatsApp conversation (SPEC.md §19, §28)."""
        return [
            Store.Attr(name, predicate=is_whatsapp_channel)
            for name in ("wa_customer_phone", "wa_status", "wa_username", "wa_window_expires_at")
        ] + [Store.Attr("wa_lead_id", lambda c: c.wa_lead_id.id, predicate=is_whatsapp_channel)]

    def _to_store_defaults(self, target):
        fields = super()._to_store_defaults(target) + self._wa_store_fields()
        if target.is_current_user(self.env):
            # [D8] Create Lead in Discuss: for salespeople, as on the conversation's form
            can_create = self.env.user.has_group("sales_team.group_sale_salesman")
            fields.append(Store.Attr("wa_can_create_lead", can_create, predicate=is_whatsapp_channel))
        return fields

    def _sync_field_names(self):
        # pushed to the members whenever they change, e.g. the window reopening
        field_names = super()._sync_field_names()
        field_names[None] += self._wa_store_fields()
        return field_names

    def _types_allowing_seen_infos(self):
        return super()._types_allowing_seen_infos() + ["whatsapp"]

    def _types_allowing_unfollow(self):
        return super()._types_allowing_unfollow() + ["whatsapp"]

    # ------------------------------------------------------------------
    # Conversation lookup and creation (SPEC.md §8, §22, §42, §43)
    # ------------------------------------------------------------------

    @api.model
    def _wa_find_conversation(self, account, bsuid=False, wa_id=False):
        """The customer's current conversation: BSUID first, then WhatsApp ID (R16)."""
        Channel = self.sudo().with_context(active_test=False)
        base = [("channel_type", "=", "whatsapp"), ("wa_account_id", "=", account.id)]
        for field, value in (("wa_bsuid", bsuid), ("wa_id", wa_id)):
            if not value:
                continue
            for status in ("open", "closed"):
                channel = Channel.search(
                    base + [(field, "=", value), ("wa_status", "=", status)],
                    order="wa_last_message_at desc, id desc", limit=1,
                )
                if channel:
                    return channel
        return Channel.browse()

    @api.model
    def _wa_get_or_create_conversation(self, account, identity):
        """Return the open conversation for ``identity``, reopening or creating it.

        ``identity`` holds bsuid, parent_bsuid, wa_id, phone (E.164), name and
        username, whichever the webhook carried.
        """
        bsuid, wa_id = identity.get("bsuid"), identity.get("wa_id")
        channel = self._wa_find_conversation(account, bsuid=bsuid, wa_id=wa_id)
        if channel:
            channel._wa_update_identity(identity)
            if channel.wa_status == "closed":
                channel._wa_reopen()
            return channel, False
        partner = self.env["res.partner"]._wa_find_or_create(
            bsuid=bsuid, phone=identity.get("phone"), name=identity.get("name"),
            username=identity.get("username"),
        )
        channel = self._wa_create_conversation(account, partner, identity)
        return channel, True

    @api.model
    def _wa_create_conversation(self, account, partner, identity, members=None):
        """Create a WhatsApp conversation.

        ``members`` defaults to the account's Notify users (Mode A, §9); Lead
        Routing passes the owner instead (§21).
        """
        if members is None:
            members = self._wa_default_members(account)
        bsuid, wa_id = identity.get("bsuid"), identity.get("wa_id")
        vals = {
            "name": partner.display_name or identity.get("phone") or bsuid,
            "channel_type": "whatsapp",
            "wa_account_id": account.id,
            "wa_customer_key": self._wa_key(bsuid, wa_id),
            "wa_bsuid": bsuid or False,
            "wa_parent_bsuid": identity.get("parent_bsuid") or False,
            "wa_id": wa_id or False,
            "wa_customer_phone": identity.get("phone") or False,
            "wa_username": identity.get("username") or False,
            "wa_partner_id": partner.id,
            "wa_status": "open",
            "channel_member_ids": [
                Command.create({"partner_id": p.id}) for p in members.partner_id
            ],
        }
        creator = self.env.user.partner_id
        channel = self.sudo().create(vals)
        # discuss.channel.create() always adds the current user; the processing
        # cron runs as OdooBot, who is not part of the conversation.
        if creator not in members.partner_id:
            channel.channel_member_ids.filtered(lambda m: m.partner_id == creator).sudo().unlink()
        # Notify users start as listeners: once someone replies they are muted
        # for 15 days (R6).
        channel.channel_member_ids.sudo().write({"wa_participant": False})
        return channel

    @api.model
    def _wa_default_members(self, account):
        return account.notify_user_ids

    @api.model
    def _wa_key(self, bsuid, wa_id):
        return bsuid or (f"wa:{wa_id}" if wa_id else False)

    def _wa_update_identity(self, identity):
        """Keep the conversation and contact up to date with what WhatsApp sent (R16)."""
        self.ensure_one()
        vals = {}
        bsuid = identity.get("bsuid")
        if bsuid and self.wa_bsuid != bsuid:
            vals.update({"wa_bsuid": bsuid, "wa_customer_key": bsuid})
        for key, field in (("parent_bsuid", "wa_parent_bsuid"), ("wa_id", "wa_id"),
                           ("phone", "wa_customer_phone"), ("username", "wa_username")):
            value = identity.get(key)
            if value and self[field] != value:
                vals[field] = value
        if vals:
            self.sudo().write(vals)
        partner = self.wa_partner_id.sudo()
        if partner:
            pvals = {}
            if bsuid and partner.wa_bsuid != bsuid:
                pvals["wa_bsuid"] = bsuid
            if identity.get("username") and partner.wa_username != identity["username"]:
                pvals["wa_username"] = identity["username"]
            if identity.get("phone") and not partner.phone:
                pvals["phone"] = identity["phone"]
                self._wa_flag_possible_duplicates(partner, identity["phone"])
            if pvals:
                partner.write(pvals)

    def _wa_flag_possible_duplicates(self, partner, phone):
        """A phone number arrived for a contact created without one (R16): flag, never merge."""
        others = self.env["res.partner"].sudo().search([
            ("phone_sanitized", "=", phone), ("id", "!=", partner.id),
        ], limit=5)
        if others:
            links = Markup(", ").join(o._get_html_link() for o in others)
            self.sudo().message_post(
                body=Markup("%s %s") % (
                    self.env._("This customer's phone number also belongs to"), links,
                ),
                message_type="notification",
            )

    def _wa_reopen(self):
        """A closed conversation gets a new message: reopen it (SPEC.md §44)."""
        for channel in self.sudo():
            channel.wa_status = "open"
        self.env.flush_all()

    # ------------------------------------------------------------------
    # Incoming messages (SPEC.md §8, §26, R20)
    # ------------------------------------------------------------------

    def _wa_receive(self, account, message, contact, identity):
        """Post one incoming WhatsApp message in this conversation.

        Returns the ``whatsapp_connector.message`` created, or an empty
        recordset for messages that are not posted (reactions, system).
        """
        self.ensure_one()
        WaMessage = self.env["whatsapp_connector.message"].sudo()
        msg_type = message.get("type")
        timestamp = self._wa_timestamp(message.get("timestamp"))
        partner = self.wa_partner_id

        if msg_type == "reaction":
            self._wa_receive_reaction(account, message.get("reaction") or {}, partner)
            return WaMessage
        if msg_type == "system":
            self._wa_receive_system(message.get("system") or {})
            return WaMessage

        if message.get("referral") and not self.wa_referral:
            self.sudo().wa_referral = message["referral"]

        body, media = self._wa_incoming_body(message)
        parent = self._wa_find_mail_message(account, (message.get("context") or {}).get("id"))
        self._wa_apply_notify_rule(timestamp)

        wa_message = WaMessage.create({
            "account_id": account.id,
            "channel_id": self.id,
            "external_message_id": message.get("id"),
            "direction": "inbound",
            "message_type": msg_type,
            "sender": identity.get("phone") or identity.get("bsuid"),
            "recipient": account.phone_number or account.phone_number_id,
            "body": body,
            "media_id": (media or {}).get("id"),
            "context_message_id": (message.get("context") or {}).get("id"),
            "status": "received",
            "wa_timestamp": timestamp,
            "media_download_state": "pending" if media else "none",
            "error_code": str((message.get("errors") or [{}])[0].get("code") or "") or False,
            "error_title": (message.get("errors") or [{}])[0].get("title") or False,
        })
        attachments = self.env["ir.attachment"]
        if media:
            attachments = wa_message._wa_download_media(media)

        post_values = {
            "body": plaintext2html(body) if body else "",
            "message_type": "whatsapp_message",
            "subtype_xmlid": "mail.mt_comment",
            "author_id": partner.id,
            "date": timestamp,
            "attachment_ids": attachments.ids,
        }
        if parent:
            post_values["parent_id"] = parent.id
        mail_message = self.sudo().with_context(mail_create_nosubscribe=True).message_post(**post_values)
        wa_message.mail_message_id = mail_message
        if attachments:
            attachments.sudo().write({"res_model": "discuss.channel", "res_id": self.id})
        # max(): Meta does not guarantee the order of webhooks (R28)
        self.sudo().write({
            "wa_last_message_at": max(filter(None, [self.wa_last_message_at, timestamp])),
            "wa_last_customer_message_at": max(
                filter(None, [self.wa_last_customer_message_at, timestamp]),
            ),
        })
        return wa_message

    @api.model
    def _wa_timestamp(self, value):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            return fields.Datetime.now()

    @api.model
    def _wa_incoming_body(self, message):
        """Text shown in Discuss for each Meta message type (R20), and the media, if any."""
        msg_type = message.get("type")
        content = message.get(msg_type) or {}
        tr = self.env._
        if msg_type == "text":
            return content.get("body") or "", None
        if msg_type in MEDIA_TYPES:
            return content.get("caption") or "", content
        if msg_type == "location":
            parts = [content.get("name"), content.get("address")]
            lat, lng = content.get("latitude"), content.get("longitude")
            if lat is not None and lng is not None:
                parts.append(f"https://maps.google.com/?q={lat},{lng}")
            return "\n".join(p for p in parts if p) or tr("Location"), None
        if msg_type == "contacts":
            lines = []
            for shared in message.get("contacts") or []:
                name = (shared.get("name") or {}).get("formatted_name") or ""
                phones = ", ".join(p.get("phone") for p in shared.get("phones") or [] if p.get("phone"))
                lines.append(" ".join(filter(None, [name, phones])))
            return tr("Shared contact: %s", "; ".join(lines)), None
        if msg_type == "button":
            return content.get("text") or content.get("payload") or "", None
        if msg_type == "interactive":
            reply = content.get(content.get("type")) or {}
            return reply.get("title") or reply.get("id") or "", None
        if msg_type == "order":
            items = content.get("product_items") or []
            lines = [
                f"{item.get('quantity')} × {item.get('product_retailer_id')} "
                f"({item.get('item_price')} {item.get('currency') or ''})".strip()
                for item in items
            ]
            text = content.get("text") or ""
            return "\n".join([tr("Order:")] + lines + ([text] if text else [])), None
        error = (message.get("errors") or [{}])[0]
        return tr("Message not supported by WhatsApp's API: %s", error.get("title") or msg_type), None

    @api.model
    def _wa_find_mail_message(self, account, external_id):
        if not external_id:
            return self.env["mail.message"]
        wa = self.env["whatsapp_connector.message"].sudo().search([
            ("account_id", "=", account.id), ("external_message_id", "=", external_id),
        ], limit=1)
        return wa.mail_message_id

    def _wa_receive_reaction(self, account, reaction, partner):
        target = self._wa_find_mail_message(account, reaction.get("message_id"))
        if not target:
            return
        guest = self.env["mail.guest"]
        emoji = reaction.get("emoji")
        target = target.sudo()
        # an empty emoji removes the customer's reaction
        existing = self.env["mail.message.reaction"].sudo().search([
            ("message_id", "=", target.id), ("partner_id", "=", partner.id),
        ])
        for old in existing:
            if old.content != emoji:
                target._message_reaction(old.content, "remove", partner, guest)
        if emoji:
            target._message_reaction(emoji, "add", partner, guest)

    def _wa_receive_system(self, system):
        """Identity changes (R16): update the conversation, never create a new one."""
        identity = {}
        if system.get("type") == "user_changed_number" and system.get("wa_id"):
            identity["wa_id"] = system["wa_id"]
            identity["phone"] = self.env["res.partner"]._wa_normalize_phone(system["wa_id"])
        if system.get("user_id"):
            identity["bsuid"] = system["user_id"]
        if system.get("parent_user_id"):
            identity["parent_bsuid"] = system["parent_user_id"]
        if identity:
            self._wa_update_identity(identity)
        if system.get("body"):
            self.sudo().message_post(body=system["body"], message_type="notification")

    def _wa_apply_notify_rule(self, timestamp):
        """Odoo's 15-day rule (SPEC.md §10, R6).

        Members are notified by Discuss core. After 15 days without a reply from
        a user, every Notify user is notified again: listeners' mutes have
        expired by then, and Notify users who left the conversation come back.
        """
        self.ensure_one()
        if self.wa_account_id.routing_mode == "lead":
            return  # routed conversations notify their owner (§46)
        last_reply = self.wa_last_user_message_at
        if last_reply and timestamp - last_reply <= NOTIFY_ALL_AFTER:
            return
        members = self.channel_member_ids
        missing = self._wa_default_members(self.wa_account_id).partner_id - members.partner_id
        if missing:
            self.sudo()._add_members(partners=missing, post_joined_message=False)
            self.channel_member_ids.filtered(
                lambda m: m.partner_id in missing,
            ).sudo().write({"wa_participant": False})
        members.filtered(lambda m: m.mute_until_dt and not m.wa_participant).sudo().write(
            {"mute_until_dt": False},
        )

    # ------------------------------------------------------------------
    # Messages sent from Discuss (SPEC.md §27, §28, R34, R36)
    # ------------------------------------------------------------------

    def message_post(self, *, message_type="notification", **kwargs):
        """A user's message in a WhatsApp conversation is sent to the customer.

        Discuss posts it as a comment (or already as a WhatsApp message); it
        becomes a WhatsApp message, checked against Meta's rules first so
        nothing is posted that cannot be sent.
        """
        if (
            len(self) == 1
            and self.channel_type == "whatsapp"
            and message_type in ("comment", "whatsapp_message")
            and not self.env.context.get("wa_skip_send")
            and not kwargs.get("author_id")
            and self.env.user._is_internal()
        ):
            attachments = self.env["ir.attachment"].browse(kwargs.get("attachment_ids") or [])
            text = self._wa_plain_text(kwargs.get("body"))
            self._wa_check_can_send(attachments, text)
            if self.wa_status == "closed":
                self._wa_reopen()  # SPEC.md §44
            message = super().message_post(message_type="whatsapp_message", **kwargs)
            self._wa_queue_outgoing(message, attachments)
            return message
        return super().message_post(message_type=message_type, **kwargs)

    def _wa_check_can_send(self, attachments, text=""):
        """Refuse what Meta would refuse (R34, R36, R22), before anything is posted."""
        self.ensure_one()
        tr = self.env._
        if not text and not attachments:
            raise UserError(tr("There is nothing to send."))
        if len(text) > TEXT_MAX_LENGTH:
            raise UserError(tr(
                "WhatsApp messages are limited to %(limit)s characters; this one has %(length)s.",
                limit=TEXT_MAX_LENGTH, length=len(text),
            ))
        if not self._wa_window_open():
            raise UserError(tr(
                "More than 24 hours have passed since the customer's last message, so WhatsApp only "
                "accepts templates. Send a template to restart the conversation.",
            ))
        if not self._wa_recipient():
            raise UserError(tr(
                "This customer has no phone number, and sending to business-scoped user IDs is not "
                "enabled on the WhatsApp account.",
            ))
        for attachment in attachments:
            WaMessage = self.env["whatsapp_connector.message"]
            error = WaMessage._wa_media_problem(attachment)
            if error:
                raise UserError(error)

    @api.model
    def _wa_plain_text(self, body):
        """WhatsApp text for a Discuss body.

        Discuss turns typed URLs into links whose text is the URL, so links
        keep their text instead of getting numbered footnotes.
        """
        return html2plaintext(body or "", include_references=False).strip()

    def _wa_recipient(self):
        """Meta addressing (R36): the phone in E.164 with "+", else the BSUID if enabled."""
        self.ensure_one()
        if self.wa_customer_phone:
            return {"to": self.wa_customer_phone}
        if self.wa_bsuid and self.wa_account_id.bsuid_sending_enabled:
            return {"recipient": self.wa_bsuid}
        return {}

    def _wa_queue_outgoing(self, message, attachments):
        """Create the WhatsApp messages to send for one Discuss message and wake the sender."""
        self.ensure_one()
        WaMessage = self.env["whatsapp_connector.message"].sudo()
        text = self._wa_plain_text(message.body)
        context_id = False
        if message.parent_id:
            context_id = message.parent_id.sudo().wa_message_ids[:1].external_message_id
        base = {
            "account_id": self.wa_account_id.id,
            "channel_id": self.id,
            "mail_message_id": message.id,
            "direction": "outbound",
            "status": "queued",
            "sender": self.wa_account_id.phone_number or self.wa_account_id.phone_number_id,
            "recipient": self.wa_customer_phone or self.wa_bsuid,
            "context_message_id": context_id,
        }
        to_send = WaMessage
        caption_used = False
        for attachment in attachments:
            media_type = WaMessage._wa_media_type(attachment)
            caption = ""
            if text and not caption_used and media_type in ("image", "video", "document"):
                caption, caption_used = text, True
            to_send |= WaMessage.create({
                **base, "message_type": media_type, "body": caption or False, "attachment_id": attachment.id,
            })
        if text and not caption_used:
            to_send |= WaMessage.create({**base, "message_type": "text", "body": text})
        self._wa_after_user_message(message.author_id)
        # Discuss announced the message before it was queued: send its status after
        message._wa_notify_delivery()
        to_send._wa_trigger_send()
        return to_send

    def _wa_after_user_message(self, author):
        """A user replied: they take part, the other listeners are muted for 15 days (R6)."""
        self.ensure_one()
        now = fields.Datetime.now()
        self.sudo().write({"wa_last_user_message_at": now, "wa_last_message_at": now})
        if self.wa_account_id.routing_mode == "lead":
            return
        members = self.sudo().channel_member_ids
        mine = members.filtered(lambda m: m.partner_id == author)
        mine.write({"wa_participant": True, "mute_until_dt": False})
        listeners = members.filtered(lambda m: not m.wa_participant)
        listeners.write({"mute_until_dt": now + NOTIFY_ALL_AFTER})
        listeners._notify_mute()

    # ------------------------------------------------------------------
    # Company-initiated conversations (SPEC.md §8.1, R5)
    # ------------------------------------------------------------------

    @api.model
    def _wa_conversation_for_partner(self, account, partner, user):
        """The conversation to send a template to ``partner`` from, opening one if needed.

        The sender becomes a member (Mode A: the chat window pops up for them
        when the customer answers).
        """
        phone = self.env["res.partner"]._wa_normalize_phone(partner.phone)
        bsuid = partner.wa_bsuid
        if not phone and not bsuid:
            raise UserError(self.env._("%s has no phone number to send WhatsApp messages to.", partner.display_name))
        wa_id = phone.lstrip("+") if phone else False
        channel = self._wa_find_conversation(account, bsuid=bsuid, wa_id=wa_id)
        if not channel:
            channel = self.sudo().search([
                ("channel_type", "=", "whatsapp"), ("wa_account_id", "=", account.id),
                ("wa_partner_id", "=", partner.id), ("wa_status", "=", "open"),
            ], limit=1)
        identity = {"bsuid": bsuid, "wa_id": wa_id, "phone": phone}
        if channel:
            if channel.wa_status == "closed":
                channel._wa_reopen()
            if user.partner_id not in channel.channel_member_ids.partner_id:
                channel._add_members(partners=user.partner_id, post_joined_message=False)
            return channel
        channel = self._wa_create_conversation(account, partner, identity, members=user)
        channel.channel_member_ids.filtered(
            lambda m: m.partner_id == user.partner_id,
        ).sudo().wa_participant = True
        return channel

    @api.model
    def _wa_conversations_action(self, domain):
        action = self.env["ir.actions.act_window"]._for_xml_id("whatsapp_connector.action_whatsapp_conversations")
        action["domain"] = [("channel_type", "=", "whatsapp"), *domain]
        action["context"] = {}  # all of them, open or closed
        return action

    def _wa_assign_sender(self, user, record):
        """Lead Routing: the sender owns a conversation they start with a template (D2, D6).

        Filled in by the Lead Routing step; nothing to do in No Lead Routing.
        """
        return

    # ------------------------------------------------------------------
    # [D8] Create lead from a conversation
    # ------------------------------------------------------------------

    def action_wa_create_lead(self):
        """Link the conversation to the customer's open lead, or create one (SPEC.md §13, D8)."""
        self.ensure_one()
        if self.wa_lead_id:
            return self._wa_lead_action(self.wa_lead_id)
        lead = self._wa_find_lead()
        if lead and not lead.has_access("read"):
            # §13: never a duplicate, and never link a lead this user cannot open
            raise UserError(self.env._(
                "This customer already has an open lead, assigned to %s. Ask them or a sales "
                "manager to link it to this conversation.",
                lead.sudo().user_id.name or self.env._("nobody"),
            ))
        created = not lead
        if created:
            lead = self.env["crm.lead"].create(self._wa_lead_values(self.env.user))
        self.sudo().wa_lead_id = lead
        link = lead._get_html_link()  # Markup: the translation is escaped around it
        if created:
            note = self.env._("Lead %s created from this conversation.", link)
        else:
            note = self.env._("This conversation is linked to the lead %s.", link)
        self.sudo().with_context(wa_skip_send=True).message_post(body=note, message_type="notification")
        return self._wa_lead_action(lead)

    def _wa_lead_action(self, lead):
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": lead.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def _wa_find_lead(self):
        """An open lead of the same customer: BSUID, phone or contact (§15 step 3, R14, R16).

        Searched among all leads, whoever they belong to, so none is duplicated;
        returned in the caller's environment.
        """
        self.ensure_one()
        clauses = []
        if self.wa_bsuid:
            clauses.append([("wa_bsuid", "=", self.wa_bsuid)])
        if self.wa_customer_phone:
            clauses.append([("phone_sanitized", "=", self.wa_customer_phone)])
        if self.wa_partner_id:
            clauses.append([("partner_id", "=", self.wa_partner_id.id)])
        if not clauses:
            return self.env["crm.lead"]
        domain = Domain.OR(clauses) & Domain("won_status", "=", "pending") & Domain("active", "=", True)
        lead = self.env["crm.lead"].sudo().search(domain, order="id desc", limit=1)
        return lead.with_env(self.env)

    def _wa_lead_values(self, user):
        self.ensure_one()
        partner = self.wa_partner_id
        name = partner.name or self.wa_customer_phone or (f"@{self.wa_username}" if self.wa_username else self.wa_bsuid)
        return {
            # no "type": crm's default makes it an opportunity when Leads are disabled
            "name": f"WhatsApp — {name}",
            "partner_id": partner.id or False,
            "contact_name": partner.name or False,
            "phone": self.wa_customer_phone or partner.phone or False,
            "email_from": partner.email or False,
            "source_id": self.env.ref("whatsapp_connector.utm_source_whatsapp").id,
            "user_id": user.id or False,
            "wa_bsuid": self.wa_bsuid or False,
            "wa_username": self.wa_username or False,
        }
