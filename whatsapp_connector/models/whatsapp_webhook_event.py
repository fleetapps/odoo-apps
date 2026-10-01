import json
import logging
import traceback

from odoo import api, fields, models
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

_logger = logging.getLogger(__name__)

BATCH_SIZE = 50


class WhatsappWebhookEvent(models.Model):
    """One webhook POST from Meta, stored before anything else happens.

    Meta: "There are no APIs for fetching historical webhook data, so capture
    and store webhook payloads accordingly." (SPEC.md §33, R35). The endpoint
    stores the body, answers 200 and a cron processes it (R27).
    """

    _name = "whatsapp_connector.webhook.event"
    _description = "WhatsApp Webhook Event"
    _order = "id desc"
    _rec_name = "received_at"

    received_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    raw_body = fields.Text(required=True, help="The request body exactly as Meta sent it.")
    state = fields.Selection(
        [("new", "New"), ("processed", "Processed"), ("error", "Error")],
        default="new", required=True, index=True,
    )
    error = fields.Text()
    processed_at = fields.Datetime()
    attempts = fields.Integer(default=0)

    @api.autovacuum
    def _gc_processed_events(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param(
            "whatsapp_connector.webhook_retention_days", "0",
        ) or 0)
        if days <= 0:  # 0: keep every event (the default)
            return
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        self.search([("state", "=", "processed"), ("received_at", "<", limit)]).unlink()

    # ------------------------------------------------------------------
    # Processing (SPEC.md §56)
    # ------------------------------------------------------------------

    @api.model
    def _cron_process_events(self):
        """Process stored events in order; commit after each one when run by the cron."""
        in_cron = bool(self.env.context.get("cron_id"))
        while True:
            events = self.search([("state", "=", "new")], order="id", limit=BATCH_SIZE)
            if not events:
                return
            for event in events:
                event._process()
                if in_cron:
                    remaining = self.search_count([("state", "=", "new")])
                    if self.env["ir.cron"]._commit_progress(1, remaining=remaining) <= 0:
                        return  # out of time: the cron runs again
            if not in_cron:
                return

    def action_retry(self):
        self.write({"state": "new", "error": False})
        self._process_all()

    def _process_all(self):
        for event in self.filtered(lambda e: e.state == "new"):
            event._process()

    def _process(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self._handle(json.loads(self.raw_body))
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # e.g. a user wrote in the same conversation meanwhile: not the
            # event's fault; the cron rolls back and processes it again
            raise
        except Exception:  # noqa: BLE001 - one bad event must not stop the others
            _logger.exception("WhatsApp webhook event %s failed", self.id)
            self.write({
                "state": "error",
                "error": traceback.format_exc(limit=5),
                "attempts": self.attempts + 1,
            })
            return
        self.write({
            "state": "processed", "error": False, "processed_at": fields.Datetime.now(),
            "attempts": self.attempts + 1,
        })

    def _handle(self, payload):
        if payload.get("object") != "whatsapp_business_account":
            return
        # Collect first, then process messages in WhatsApp timestamp order
        # within this event (R28); one POST can hold many updates (R35).
        inbound, statuses, others = [], [], []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                field = change.get("field")
                account = self._find_account(entry.get("id"), value)
                if field == "messages":
                    if account is None:
                        raise ValueError(
                            "No WhatsApp account for phone number ID %s"
                            % (value.get("metadata") or {}).get("phone_number_id"),
                        )
                    contacts = value.get("contacts") or []
                    for message in value.get("messages") or []:
                        inbound.append((account, message, self._contact_for(message, contacts)))
                    for status in value.get("statuses") or []:
                        statuses.append((account, status, contacts))
                else:
                    others.append((account, field, value))

        inbound.sort(key=lambda item: (int(item[1].get("timestamp") or 0), item[1].get("id") or ""))
        for account, message, contact in inbound:
            self._handle_message(account, message, contact)
        for account, status, contacts in statuses:
            self._handle_status(account, status, contacts)
        synced = set()
        for account, field, value in others:
            if field in self.TEMPLATE_FIELDS:
                if account is not None and account not in synced:
                    # one sync per account, however many template updates came
                    synced.add(account)
                    account._wa_sync_templates()
                continue
            self._handle_other(account, field, value)

    @api.model
    def _find_account(self, waba_id, value):
        """The account for an update: its business number, else its WABA (R17)."""
        Account = self.env["whatsapp_connector.account"].sudo()
        phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
        if phone_number_id:
            # a number not configured in Odoo is never attributed to another
            # number of the same WhatsApp Business Account
            return Account.search([("phone_number_id", "=", phone_number_id)], limit=1) or None
        if waba_id:
            account = Account.search([("waba_id", "=", waba_id)], limit=1)
            if account:
                return account
        return None

    @api.model
    def _contact_for(self, message, contacts):
        sender = message.get("from_user_id") or message.get("from")
        for contact in contacts:
            if sender and sender in (contact.get("user_id"), contact.get("wa_id")):
                return contact
        return contacts[0] if len(contacts) == 1 else {}

    @api.model
    def _identity(self, message, contact):
        """Who sent the message, from whatever Meta included (R16)."""
        profile = contact.get("profile") or {}
        wa_id = message.get("from") or contact.get("wa_id")
        return {
            "bsuid": message.get("from_user_id") or contact.get("user_id"),
            "parent_bsuid": message.get("from_parent_user_id") or contact.get("parent_user_id"),
            "wa_id": wa_id,
            "phone": self.env["res.partner"]._wa_normalize_phone(wa_id) if wa_id else False,
            "name": profile.get("name"),
            "username": profile.get("username"),
        }

    def _handle_message(self, account, message, contact):
        WaMessage = self.env["whatsapp_connector.message"].sudo()
        if message.get("id") and WaMessage.search_count([
            ("account_id", "=", account.id), ("external_message_id", "=", message["id"]),
        ]):
            return  # duplicate delivery (§24)
        if message.get("group_id"):
            return  # WhatsApp groups are out of scope (R32)
        identity = self._identity(message, contact)
        Channel = self.env["discuss.channel"].sudo()
        if message.get("type") == "system":
            system = message.get("system") or {}
            channel = Channel._wa_find_conversation(
                account,
                bsuid=system.get("previous_user_id") or identity["bsuid"],
                wa_id=identity["wa_id"],
            )
            if channel:
                channel._wa_receive(account, message, contact, identity)
            return
        if not (identity["bsuid"] or identity["wa_id"]):
            raise ValueError("Incoming message %s has no sender" % message.get("id"))
        channel, _created = Channel._wa_get_or_create_conversation(account, identity)
        channel._wa_receive(account, message, contact, identity)

    def _handle_status(self, account, status, contacts):
        """Status of a message the business sent (R19)."""
        WaMessage = self.env["whatsapp_connector.message"].sudo()
        message = WaMessage.search([
            ("account_id", "=", account.id), ("external_message_id", "=", status.get("id")),
        ], limit=1)
        if not message:
            return
        message._apply_status(
            status.get("status"),
            timestamp=self.env["discuss.channel"]._wa_timestamp(status.get("timestamp")),
            errors=status.get("errors"),
        )
        # status webhooks also carry the recipient's BSUID (R16)
        bsuid = status.get("recipient_user_id") or next(
            (c.get("user_id") for c in contacts if c.get("user_id")), False,
        )
        if bsuid and message.channel_id and not message.channel_id.wa_bsuid:
            message.channel_id._wa_update_identity({"bsuid": bsuid})

    TEMPLATE_FIELDS = (
        "message_template_status_update",
        "message_template_quality_update",
        "template_category_update",
    )

    def _handle_other(self, account, field, value):
        # Template updates (TEMPLATE_FIELDS) re-read the account's templates from
        # Meta rather than trusting the webhook's own fields: the template list is
        # the documented source. See _handle.
        if field == "user_id_update" and account is not None:
            self._handle_user_id_update(account, value)

    def _handle_user_id_update(self, account, value):
        """A customer's BSUID changed (R16, R37): re-key their conversation."""
        updates = value.get("user_id_update") or []
        if isinstance(updates, dict):
            updates = [updates]
        Channel = self.env["discuss.channel"].sudo()
        for update in updates:
            ids = update.get("user_id") or {}
            previous, current = ids.get("previous"), ids.get("current")
            if not current:
                continue
            channel = Channel._wa_find_conversation(account, bsuid=previous, wa_id=update.get("wa_id"))
            if not channel:
                continue
            identity = {"bsuid": current}
            parent = update.get("parent_user_id") or {}
            if parent.get("current"):
                identity["parent_bsuid"] = parent["current"]
            channel._wa_update_identity(identity)
