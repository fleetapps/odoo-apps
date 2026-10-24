# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The callback inbox: what Safaricom posted, exactly as it arrived.

The controller stores the raw body and answers at once; a queue job does the
work. Safaricom gives a confirmation URL a few seconds, treats anything but a
quick 200 as a failed delivery, and does not reliably deliver it again, so
nothing that can be slow (locks, reconciliation, a bus message) runs inside
the request.
"""

import ipaddress
import json
import logging

import psycopg2

from odoo import _, api, fields, models

from ..lib import daraja

_logger = logging.getLogger(__name__)

KINDS = [
    ("c2b_confirm", "C2B confirmation"),
    ("c2b_validate", "C2B validation"),
    ("stk", "STK callback"),
    ("status_result", "Transaction Status result"),
    ("status_timeout", "Transaction Status timeout"),
    ("pull", "Pull API"),
    ("b2c_result", "B2C result"),
    ("b2c_timeout", "B2C timeout"),
    ("reversal_result", "Reversal result"),
    ("reversal_timeout", "Reversal timeout"),
    ("balance_result", "Account balance result"),
    ("balance_timeout", "Account balance timeout"),
]
ACCEPTED = {"ResultCode": 0, "ResultDesc": "Accepted"}
MAX_BODY = 64 * 1024


class MpesaCallback(models.Model):
    _name = "mpesa.callback"
    _description = "M-Pesa Callback"
    _order = "received_at desc, id desc"
    _rec_name = "dedup_key"

    kind = fields.Selection(KINDS, required=True, readonly=True, index=True)
    shortcode_id = fields.Many2one("mpesa.shortcode", readonly=True, index=True,
                                   ondelete="set null")
    company_id = fields.Many2one("res.company", readonly=True, index=True)
    payload = fields.Text(readonly=True)
    remote_ip = fields.Char("Source address", readonly=True)
    ip_trusted = fields.Boolean("Trusted source", readonly=True)
    received_at = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    state = fields.Selection(
        [("new", "New"), ("done", "Processed"), ("error", "Failed"),
         ("ignored", "Ignored")], default="new", required=True, readonly=True, index=True)
    error = fields.Text(readonly=True)
    transaction_id = fields.Many2one("mpesa.transaction", readonly=True, index=True,
                                     ondelete="set null")
    dedup_key = fields.Char("Reference", readonly=True, index=True)
    duplicate = fields.Boolean(readonly=True,
                               help="Safaricom sent this one before. Processing is "
                                    "idempotent; the copy is kept for the record.")
    redacted = fields.Boolean(readonly=True)
    attempts = fields.Integer(readonly=True)

    # ------------------------------------------------------------------
    # Receiving (called from the controller, as superuser)
    # ------------------------------------------------------------------

    @api.model
    def _receive(self, kind, token, body, remote_ip):
        """Store one callback and queue its processing.

        Returns the JSON to answer with, or None for "unknown URL" (404).
        """
        shortcode = self.env["mpesa.shortcode"]._from_token(token)
        if not shortcode:
            _logger.warning("M-Pesa callback for an unknown token from %s", remote_ip)
            return None
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = None
        trusted = self._ip_trusted(remote_ip)
        vals = {
            "kind": kind,
            "shortcode_id": shortcode.id,
            "company_id": shortcode.company_id.id,
            "payload": text[:MAX_BODY],
            "remote_ip": remote_ip,
            "ip_trusted": trusted,
            "dedup_key": self._dedup_key(kind, data) if isinstance(data, dict) else False,
        }
        if not isinstance(data, dict):
            vals.update(state="error", error=_("The body is not a JSON object."))
        elif self._allowlist_enforced() and not trusted:
            vals.update(state="ignored", error=_(
                "%s is not on the Safaricom address list. Check it on the M-PESA "
                "Org Portal before processing it by hand.", remote_ip))
        if vals["dedup_key"]:
            vals["duplicate"] = bool(self.search_count([
                ("kind", "=", kind), ("dedup_key", "=", vals["dedup_key"]),
                ("company_id", "=", shortcode.company_id.id)], limit=1))
        callback = self.create(vals)
        if callback.state == "new":
            callback.with_delay(
                priority=1 if kind in ("c2b_confirm", "stk") else 10,
                description=_("M-Pesa %(kind)s %(ref)s",
                              kind=dict(KINDS)[kind], ref=callback.dedup_key or ""),
            )._process()
        if kind == "c2b_validate" and isinstance(data, dict):
            return self._validate_c2b(shortcode, data)
        return dict(ACCEPTED)

    @api.model
    def _dedup_key(self, kind, data):
        if kind in ("c2b_confirm", "c2b_validate"):
            return daraja.clean_receipt(data.get("TransID")) or False
        if kind == "stk":
            callback = (data.get("Body") or {}).get("stkCallback") or {}
            checkout = callback.get("CheckoutRequestID")
            return f"{checkout}:{callback.get('ResultCode')}" if checkout else False
        result = data.get("Result") or {}
        return result.get("ConversationID") or result.get("OriginatorConversationID") or False

    @api.model
    def _validate_c2b(self, shortcode, data):
        """Validation answer (only asked when Safaricom enabled it): accept.

        Hook for stricter rules, e.g. a paybill that knows every valid account
        number. Must answer within about 8 seconds.
        """
        return {"ResultCode": "0", "ResultDesc": "Accepted"}

    @api.model
    def _allowlist_enforced(self):
        params = self.env["ir.config_parameter"].sudo()
        return params.get_param("x_ke_mpesa.ip_allowlist_enabled") in ("1", "True", "true")

    @api.model
    def _ip_trusted(self, remote_ip):
        params = self.env["ir.config_parameter"].sudo()
        networks = params.get_param("x_ke_mpesa.ip_allowlist") or ""
        try:
            address = ipaddress.ip_address((remote_ip or "").strip())
        except ValueError:
            return False
        for entry in networks.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        return False

    # ------------------------------------------------------------------
    # Processing (queue job)
    # ------------------------------------------------------------------

    def _process(self):
        for callback in self:
            if callback.state in ("done",):
                continue
            callback.attempts += 1
            try:
                data = json.loads(callback.payload or "{}")
                with self.env.cr.savepoint():
                    tx = callback.with_company(callback.company_id or self.env.company)._dispatch(data)
            except psycopg2.OperationalError:
                # Lock or serialisation conflict with a concurrent callback:
                # queue_job runs the job again in a fresh transaction.
                raise
            except Exception as exc:  # noqa: BLE001 - kept on the record, not lost
                _logger.exception("M-Pesa callback %s failed", callback.id)
                callback.write({"state": "error", "error": str(exc)})
                continue
            callback.write({
                "state": "done",
                "error": False,
                "transaction_id": tx.id if tx else callback.transaction_id.id,
            })

    def _dispatch(self, data):
        """Route one callback body to the code that understands it."""
        self.ensure_one()
        Tx = self.env["mpesa.transaction"].sudo()
        handler = getattr(self, f"_handle_{self.kind}", None)
        if handler:
            return handler(data)
        _logger.info("M-Pesa %s callback stored without processing", self.kind)
        return Tx

    def _handle_c2b_confirm(self, data):
        return self.env["mpesa.transaction"].sudo()._from_c2b(self.shortcode_id, data)

    def _handle_c2b_validate(self, data):
        return self.env["mpesa.transaction"].sudo()

    def _handle_stk(self, data):
        return self.env["mpesa.transaction"].sudo()._stk_from_callback(self.shortcode_id, data)

    def _handle_status_result(self, data):
        return self.env["mpesa.transaction"].sudo()._from_status_result(self.shortcode_id, data)

    def _handle_status_timeout(self, data):
        return self.env["mpesa.transaction"].sudo()

    def action_process(self):
        """Process again (after a fix, or an untrusted callback checked by hand)."""
        self.filtered(lambda c: c.state != "done").write({"state": "new", "error": False})
        self._process()
        return True

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def _redact(self):
        for callback in self:
            try:
                data = json.loads(callback.payload or "{}")
            except ValueError:
                data = None
            payload = json.dumps(daraja.redact(_mask_phones(data))) if data is not None else ""
            callback.write({"payload": payload, "redacted": True})


def _mask_phones(data):
    """Mask phone numbers wherever Daraja put them, including STK metadata items."""
    if isinstance(data, dict):
        if data.get("Name") == "PhoneNumber" and "Value" in data:
            return dict(data, Value=daraja.mask_msisdn(data["Value"]))
        return {key: _mask_phones(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_mask_phones(item) for item in data]
    return data
