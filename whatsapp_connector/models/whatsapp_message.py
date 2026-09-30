from datetime import timedelta

from odoo import api, fields, models

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
