import base64
import binascii
import hashlib
import logging
import mimetypes
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.whatsapp_connector.tools.meta_api import MetaApiError, WhatsAppApi

_logger = logging.getLogger(__name__)

# Meta: "Media IDs in webhooks expire after 7 days" (R22).
MEDIA_ID_LIFETIME = timedelta(days=7)

MB = 1024 * 1024
# Meta's supported media for sending (R22): type -> {mimetype: max size}.
SUPPORTED_MEDIA = {
    "image": {"image/jpeg": 5 * MB, "image/png": 5 * MB},
    "audio": {"audio/aac": 16 * MB, "audio/amr": 16 * MB, "audio/mpeg": 16 * MB,
              "audio/mp4": 16 * MB, "audio/ogg": 16 * MB},
    "video": {"video/mp4": 16 * MB, "video/3gpp": 16 * MB},
    "document": {
        "text/plain": 100 * MB,
        "application/vnd.ms-excel": 100 * MB,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": 100 * MB,
        "application/msword": 100 * MB,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 100 * MB,
        "application/vnd.ms-powerpoint": 100 * MB,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": 100 * MB,
        "application/pdf": 100 * MB,
    },
    "sticker": {"image/webp": 500 * 1024},
}

# Order in which Meta's statuses progress. A late webhook never moves a status
# back (SPEC.md §24, R19): "delivered" after "read" is ignored.
STATUS_RANK = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}

# Meta's message time-to-live (R19): 30 days, 10 minutes for authentication
# templates. Without a "delivered" status by then, Meta says to assume the
# message was dropped.
TTL_DEFAULT = timedelta(days=30)
TTL_AUTHENTICATION = timedelta(minutes=10)

SEND_BATCH_SIZE = 200


class WhatsappMessage(models.Model):
    """WhatsApp transport data of one message (SPEC.md §25, §53.1).

    The canonical message shown in Discuss and the chatter is the linked
    ``mail.message`` (type ``whatsapp_message``); this record keeps what Meta
    sent or returned for it.
    """

    _name = "whatsapp_connector.message"
    _description = "WhatsApp Message"
    _order = "id desc"
    _rec_name = "external_message_id"

    mail_message_id = fields.Many2one("mail.message", index=True, ondelete="cascade")
    channel_id = fields.Many2one("discuss.channel", "Conversation", index=True, ondelete="cascade")
    account_id = fields.Many2one(
        "whatsapp_connector.account", required=True, index=True, ondelete="cascade",
    )
    partner_id = fields.Many2one("res.partner", "Customer", related="channel_id.wa_partner_id")
    external_message_id = fields.Char("WhatsApp Message ID", index="btree_not_null", copy=False)
    direction = fields.Selection([("inbound", "Inbound"), ("outbound", "Outbound")], required=True)
    message_type = fields.Char("Message Type", help="Meta's message type, e.g. text, image, template.")
    sender = fields.Char()
    recipient = fields.Char()
    body = fields.Text()
    media_id = fields.Char("Media ID")
    attachment_id = fields.Many2one("ir.attachment", ondelete="set null")
    template_id = fields.Many2one("whatsapp_connector.template", ondelete="set null")
    context_message_id = fields.Char("Reply To", help="WhatsApp message ID this message replies to.")
    status = fields.Selection(
        [
            ("received", "Received"),
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("read", "Read"),
            ("failed", "Failed"),
            ("dropped", "Dropped"),
        ],
        required=True, index=True,
        help="Queued: saved in Odoo, not yet accepted by Meta. Dropped: no 'delivered' status "
        "within Meta's time-to-live, which Meta says to treat as dropped.",
    )
    message_status = fields.Char(
        "Pacing Status", help="Meta's message_status, returned only for templates being paced.",
    )
    error_code = fields.Char()
    error_title = fields.Char()
    error_message = fields.Text()
    error_details = fields.Text()
    wa_timestamp = fields.Datetime("WhatsApp Timestamp")
    sent_at = fields.Datetime("Accepted by Meta")
    payload = fields.Json(help="Values of a template's placeholders, kept so a retry sends the same.")
    media_download_state = fields.Selection(
        [("none", "No media"), ("pending", "To download"), ("done", "Downloaded"),
         ("failed", "Failed")],
        default="none", required=True,
    )

    _account_external_message_unique = models.UniqueIndex(
        "(account_id, external_message_id) WHERE external_message_id IS NOT NULL",
        "This WhatsApp message is already recorded.",
    )

    def _apply_status(self, status, timestamp=None, errors=None):
        """Apply a Meta status without ever moving backwards (R19)."""
        for message in self:
            current = message.status
            if status == "failed":
                if current in ("queued", "sent"):
                    vals = {"status": "failed"}
                    vals.update(message._error_vals(errors))
                    message.write(vals)
                continue
            if current not in STATUS_RANK or status not in STATUS_RANK:
                continue
            if STATUS_RANK[status] > STATUS_RANK[current]:
                message.status = status

    @api.model
    def _error_vals(self, errors):
        error = (errors or [{}])[0] or {}
        return {
            "error_code": str(error["code"]) if error.get("code") is not None else False,
            "error_title": error.get("title") or False,
            "error_message": error.get("message") or False,
            "error_details": (error.get("error_data") or {}).get("details") or False,
        }

    @api.model
    def _cron_mark_dropped(self):
        """Mark as dropped what Meta never delivered within its time-to-live (R19)."""
        now = fields.Datetime.now()
        # accepted by Meta (sent_at) but never delivered
        sent = self.search([
            ("status", "in", ("queued", "sent")), ("sent_at", "!=", False),
            ("external_message_id", "!=", False),
        ])
        for message in sent:
            ttl = TTL_AUTHENTICATION if message.template_id.category == "authentication" else TTL_DEFAULT
            if message.sent_at + ttl < now:
                message.status = "dropped"

    # ------------------------------------------------------------------
    # Incoming media (SPEC.md §51, R22)
    # ------------------------------------------------------------------

    def _wa_download_media(self, media):
        """Download an incoming media file and return it as an attachment.

        Two steps: the media ID gives a URL valid for 5 minutes, downloaded with
        the access token. Failures stay "To download" and are retried by a cron
        until the media ID expires (7 days).
        """
        self.ensure_one()
        attachments = self.env["ir.attachment"]
        try:
            api_client = WhatsAppApi(self.account_id)
            info = api_client.get_media_url(media["id"])
            content, content_type = api_client.download_media(info["url"])
        except (MetaApiError, KeyError) as e:
            _logger.info("WhatsApp media %s not downloaded yet: %s", media.get("id"), e)
            self.sudo().media_download_state = "pending"
            return attachments
        expected = media.get("sha256") or info.get("sha256")
        if expected and not self._sha256_matches(content, expected):
            self.sudo().write({
                "media_download_state": "failed",
                "error_title": self.env._("The downloaded file does not match Meta's checksum."),
            })
            return attachments
        mimetype = (media.get("mime_type") or info.get("mime_type") or content_type or "")
        mimetype = mimetype.split(";")[0].strip() or "application/octet-stream"
        filename = media.get("filename") or self._default_filename(mimetype)
        attachment = attachments.sudo().create({
            "name": filename,
            "raw": content,
            "mimetype": mimetype,
            "res_model": "discuss.channel" if self.channel_id else False,
            "res_id": self.channel_id.id or False,
        })
        self.sudo().write({"attachment_id": attachment.id, "media_download_state": "done"})
        return attachment

    @api.model
    def _sha256_matches(self, content, expected):
        digest = hashlib.sha256(content).digest()
        if expected == digest.hex():
            return True
        try:
            return base64.b64decode(expected) == digest
        except (binascii.Error, ValueError):
            return False

    def _default_filename(self, mimetype):
        extension = mimetypes.guess_extension(mimetype) or ""
        return f"whatsapp-{self.message_type or 'file'}{extension}"

    @api.model
    def _cron_retry_media(self):
        """Retry media downloads until Meta's media ID expires (7 days)."""
        now = fields.Datetime.now()
        pending = self.search([("media_download_state", "=", "pending"), ("media_id", "!=", False)])
        for message in pending:
            if message.wa_timestamp and message.wa_timestamp + MEDIA_ID_LIFETIME < now:
                message.write({
                    "media_download_state": "failed",
                    "error_title": self.env._("The media expired at Meta before it could be downloaded."),
                })
                continue
            media = {"id": message.media_id}
            attachment = message._wa_download_media(media)
            if attachment and message.mail_message_id and message.channel_id:
                message.channel_id.sudo()._message_update_content(
                    message.mail_message_id.sudo(), body=None, attachment_ids=[attachment.id],
                    strict=False,
                )

    # ------------------------------------------------------------------
    # Outgoing messages (SPEC.md §27, §35, R19, R22, R36)
    # ------------------------------------------------------------------

    @api.model
    def _wa_media_type(self, attachment):
        mimetype = (attachment.mimetype or "").split(";")[0]
        for media_type, mimetypes_ in SUPPORTED_MEDIA.items():
            if mimetype in mimetypes_:
                return media_type
        return False

    @api.model
    def _wa_media_problem(self, attachment):
        """Why Meta would refuse this file (R22), or False."""
        media_type = self._wa_media_type(attachment)
        if not media_type:
            return self.env._(
                "WhatsApp cannot send %(name)s (%(type)s). Supported: JPEG and PNG images, "
                "MP4 and 3GP videos, AAC, AMR, MP3, M4A and OGG audio, and TXT, PDF, Word, Excel "
                "and PowerPoint documents.",
                name=attachment.name, type=attachment.mimetype or "?",
            )
        limit = SUPPORTED_MEDIA[media_type][(attachment.mimetype or "").split(";")[0]]
        if (attachment.file_size or 0) > limit:
            return self.env._(
                "%(name)s is too large for WhatsApp: the limit for this type is %(limit)s MB.",
                name=attachment.name, limit=round(limit / MB, 1),
            )
        return False

    def _wa_trigger_send(self):
        if self:
            self.env.ref("whatsapp_connector.ir_cron_send_messages").sudo()._trigger()

    @api.model
    def _cron_send_queued(self):
        """Send queued messages in order.

        In the cron each send is committed at once: a later error must not roll
        back a message Meta already accepted, or the next run would send it again.
        """
        in_cron = bool(self.env.context.get("cron_id"))
        domain = [("status", "=", "queued"), ("external_message_id", "=", False),
                  ("direction", "=", "outbound")]
        for message in self.search(domain, order="id", limit=SEND_BATCH_SIZE):
            message._wa_send()
            if in_cron:
                remaining = self.search_count(domain)
                if self.env["ir.cron"]._commit_progress(1, remaining=remaining) <= 0:
                    return  # out of time: the cron runs again

    def _wa_send(self):
        """Send one queued message to Meta and record its answer."""
        self.ensure_one()
        channel = self.channel_id
        recipient = channel._wa_recipient() if channel else {}
        if not recipient:
            self._wa_mark_send_failed(
                MetaApiError(self.env._("No phone number or business-scoped user ID to send to.")),
            )
            return False
        try:
            with self.env.cr.savepoint():
                api_client = WhatsAppApi(self.account_id)
                payload = dict(recipient, **self._wa_content(api_client))
                if self.context_message_id:
                    payload["context"] = {"message_id": self.context_message_id}
                result = api_client.send_message(payload)
        except MetaApiError as e:
            self._wa_mark_send_failed(e)
            return False
        except Exception as e:
            # anything else (a missing file, a bug) fails this message only
            _logger.exception("WhatsApp message %s could not be sent", self.id)
            self._wa_mark_send_failed(e)
            return False
        sent = (result.get("messages") or [{}])[0]
        if not sent.get("id"):
            # never left queued without an ID: the next run would send it again
            self._wa_mark_send_failed(MetaApiError(self.env._(
                "Meta accepted the request but returned no message ID; check the conversation "
                "on the phone before retrying.",
            )))
            return False
        self.sudo().write({
            "external_message_id": sent.get("id"),
            "message_status": sent.get("message_status") or False,
            "sent_at": fields.Datetime.now(),
        })
        # written now, so an error here is not mistaken for the identity update's below
        self.flush_recordset()
        answer = (result.get("contacts") or [{}])[0]
        identity = {"wa_id": answer.get("wa_id"), "bsuid": answer.get("user_id")}
        if channel and any(identity.values()):
            try:
                with self.env.cr.savepoint():
                    channel._wa_update_identity({k: v for k, v in identity.items() if v})
            except Exception:
                # the message is sent: an identity conflict must not undo that
                _logger.exception("WhatsApp conversation %s: identity not updated", channel.id)
        return True

    def _wa_content(self, api_client):
        """Meta's message object for this record (without the recipient)."""
        if self.message_type == "template":
            return {"type": "template", "template": self.template_id._wa_payload(self.payload or {}, api_client)}
        if self.attachment_id:
            attachment = self.attachment_id.sudo()
            media_id = api_client.upload_media(
                attachment.name, attachment.mimetype, attachment.raw,
            )
            media = {"id": media_id}
            if self.body and self.message_type in ("image", "video", "document"):
                media["caption"] = self.body
            if self.message_type == "document":
                media["filename"] = attachment.name
            return {"type": self.message_type, self.message_type: media}
        return {"type": "text", "text": {"body": self.body or "", "preview_url": True}}

    def _wa_mark_send_failed(self, error):
        if isinstance(error, MetaApiError):
            errors = error.as_error_list()
        else:
            errors = [{"title": self.env._("Could not send the message"), "message": str(error)}]
        self.sudo().write({"status": "failed", **self._error_vals(errors)})
        if isinstance(error, MetaApiError) and error.is_token_error:
            self.account_id.sudo().write({
                "connection_state": "token_error",
                "connection_message": str(error),
            })

    def action_retry(self):
        """Send a failed message again (SPEC.md §35)."""
        for message in self.filtered(lambda m: m.status in ("failed", "dropped") and m.direction == "outbound"):
            if message.message_type != "template" and message.channel_id and not message.channel_id._wa_window_open():
                raise UserError(self.env._(
                    "More than 24 hours have passed since the customer's last message: "
                    "only a template can be sent now.",
                ))
            message.sudo().write({
                "status": "queued", "external_message_id": False, "error_code": False,
                "error_title": False, "error_message": False, "error_details": False,
            })
        self._wa_trigger_send()
