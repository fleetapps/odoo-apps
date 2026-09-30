import base64
import binascii
import hashlib
import logging
import mimetypes
from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.whatsapp_connector.tools.meta_api import MetaApiError, WhatsAppApi

_logger = logging.getLogger(__name__)

# Meta: "Media IDs in webhooks expire after 7 days" (R22).
MEDIA_ID_LIFETIME = timedelta(days=7)

# Order in which Meta's statuses progress. A late webhook never moves a status
# back (SPEC.md §24, R19): "delivered" after "read" is ignored.
STATUS_RANK = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}

# Meta's message time-to-live (R19): 30 days, 10 minutes for authentication
# templates. Without a "delivered" status by then, Meta says to assume the
# message was dropped.
TTL_DEFAULT = timedelta(days=30)
TTL_AUTHENTICATION = timedelta(minutes=10)


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
        sent = self.search([("status", "=", "sent"), ("sent_at", "!=", False)])
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
