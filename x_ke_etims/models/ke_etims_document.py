# -*- coding: utf-8 -*-
"""The eTIMS queue: one row for each thing that has to reach KRA.

Posting an invoice never talks to KRA. It puts a document here, and the
document is sent by the queue's cron, by "Send now", or by the short
synchronous attempt made when the invoice is about to be emailed. Whatever
sends it, it goes through ``_ke_etims_process_one`` below, which is the only
place a KRA invoice number is drawn and the only place a backend is called for
a sale.

The invoice number is the whole difficulty
------------------------------------------
KRA wants each branch's invoice numbers gapless and in order, and the same
sale must never be signed under two numbers. So:

1. Everything that can be checked locally is checked *before* a number is
   drawn: registered products, mapped taxes, the customer's PIN, the credit
   note's original. A document that fails here fails without consuming a
   number.
2. The number is drawn under a lock on the device row and committed before
   the request leaves, so a crash after KRA signed cannot lose it.
3. Every retry resends the same number with the same content. If KRA did
   record the first attempt it refuses the second as a duplicate, which is
   recoverable; signing it twice would not be.
4. A number is handed back only when the other side definitely did not record
   it (it answered with a rejection, or the connection was never made), and
   only if it is still the last one drawn.
5. On a backend that refuses numbers out of order, a document holding an
   unsigned number blocks every later document of that device until it is
   signed or released. The device form names it.

Retries back off exponentially with jitter. After eight failed attempts the
document is marked failed and the accountant gets an activity; a rejection
that cannot succeed on retry (a missing reason code, a refused PIN) fails at
once rather than eight times.
"""

import json
import logging
import random
import uuid
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models, modules, tools
from odoo.exceptions import UserError

from ..backends import REGISTRY, EtimsError, NotSupported

_logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
#: Seconds. Doubles with each attempt, capped at an hour, then jittered.
BASE_DELAY = 60
MAX_DELAY = 3600
#: A document left "sending" this long was abandoned by a crashed worker.
STALE_SENDING = timedelta(minutes=10)
#: Seconds, for a send someone is waiting on.
INTERACTIVE_TIMEOUT = 15.0

STATES = [
    ("to_send", "To Send"),
    ("sending", "Sending"),
    ("sent", "Signed"),
    ("pending", "Pending"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]


class KeEtimsDocument(models.Model):
    _name = "ke.etims.document"
    _description = "eTIMS Document"
    _inherit = ["mail.thread"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    # Not check_company: a branch trading under head office's KRA branch ID
    # signs with head office's device. res.company._ke_etims_device decides.
    device_id = fields.Many2one("ke.etims.device", index=True, ondelete="restrict")
    environment = fields.Selection(related="device_id.environment")
    kind = fields.Selection(
        [("sale", "Sale"), ("credit_note", "Credit Note")],
        required=True, index=True)
    res_model = fields.Char(required=True, index=True)
    res_id = fields.Many2oneReference(model_field="res_model", required=True, index=True)
    move_id = fields.Many2one("account.move", index="btree_not_null", ondelete="cascade")
    original_document_id = fields.Many2one("ke.etims.document", string="Original")

    state = fields.Selection(STATES, default="to_send", required=True, index=True,
                             copy=False)
    kra_invoice_no = fields.Integer(
        string="KRA Invoice No.", copy=False, index=True, readonly=True,
        help="Drawn from the device's sequence when the document is first "
             "sent, and reused on every retry.")
    idempotency_key = fields.Char(required=True, copy=False, readonly=True,
                                  default=lambda self: uuid.uuid4().hex)
    attempts = fields.Integer(readonly=True, copy=False)
    next_try = fields.Datetime(readonly=True, copy=False, index=True)
    last_error = fields.Text(readonly=True, copy=False)
    last_error_code = fields.Char(readonly=True, copy=False)
    may_have_been_processed = fields.Boolean(
        readonly=True, copy=False,
        help="The last failure happened after the request may have arrived, "
             "so the number is kept and the same request will be sent again.")
    confirmed_at = fields.Datetime(
        readonly=True, copy=False,
        help="The confirmation time sent to KRA. Fixed at the first attempt so "
             "every retry sends exactly the same sale.")

    reason_code = fields.Char(string="Credit Note Reason", readonly=True)
    receipt_no = fields.Integer(string="Receipt No.", readonly=True, copy=False)
    total_receipt_no = fields.Integer(string="Total Receipt No.", readonly=True, copy=False)
    internal_data = fields.Char(readonly=True, copy=False)
    signature = fields.Char(string="Receipt Signature", readonly=True, copy=False)
    scu_id = fields.Char(string="SCU ID", readonly=True, copy=False)
    mrc_no = fields.Char(string="MRC No.", readonly=True, copy=False)
    signed_at = fields.Datetime(readonly=True, copy=False)
    qr_url = fields.Char(string="QR Code URL", readonly=True, copy=False)
    provider_ref = fields.Char(string="Provider Reference", readonly=True, copy=False)
    signed_environment = fields.Char(readonly=True, copy=False,
                                     help="The environment the device was in when it signed.")
    totals_json = fields.Text(readonly=True, copy=False,
                              help="Taxable amount and tax per KRA tax type, as sent.")
    receipt_label = fields.Char(compute="_compute_receipt_label")

    log_ids = fields.One2many("ke.etims.log", "document_id",
                              groups="x_ke_etims.group_ke_etims_manager")

    _active_document_unique = models.UniqueIndex(
        "(res_model, res_id, kind) WHERE state != 'cancelled'",
        "This record already has an eTIMS document of this kind.")

    # ------------------------------------------------------------ computes

    @api.depends("kind", "res_model", "res_id", "kra_invoice_no")
    def _compute_name(self):
        kinds = dict(self._fields["kind"]._description_selection(self.env))
        for document in self:
            source = document._ke_etims_source()
            label = source.display_name if source else "%s,%s" % (document.res_model,
                                                                document.res_id)
            number = " #%s" % document.kra_invoice_no if document.kra_invoice_no else ""
            document.name = "%s %s%s" % (kinds.get(document.kind, document.kind), label, number)

    @api.depends("kind")
    def _compute_receipt_label(self):
        """KRA's receipt label: receipt type then transaction type, e.g. NS."""
        for document in self:
            document.receipt_label = "NC" if document.kind == "credit_note" else "NS"

    # ------------------------------------------------------------- helpers

    @api.model
    def _ke_etims_can_commit(self):
        return not (tools.config["test_enable"] or modules.module.current_test)

    def _ke_etims_source(self):
        self.ensure_one()
        if not self.res_model or not self.res_id or self.res_model not in self.env:
            return None
        return self.env[self.res_model].browse(self.res_id).exists() or None

    def _ke_etims_needs_number(self):
        """Whether this kind takes a number from the device's sales sequence."""
        return self.kind in ("sale", "credit_note")

    def _ke_etims_lock(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "SELECT id FROM ke_etims_document WHERE id = %s FOR NO KEY UPDATE NOWAIT",
                    [self.id])
        except Exception:  # psycopg2.errors.LockNotAvailable
            return False
        self.invalidate_recordset()
        return True

    def _ke_etims_commit(self):
        if self._ke_etims_can_commit():
            self.env.cr.commit()

    # ------------------------------------------------------------- enqueue

    @api.model
    def _ke_etims_enqueue(self, records, kind, **values):
        """A document for each record that has no live one of this kind yet."""
        created = self.browse()
        for record in records:
            existing = self.sudo().search([
                ("res_model", "=", record._name), ("res_id", "=", record.id),
                ("kind", "=", kind), ("state", "!=", "cancelled")], limit=1)
            if existing:
                created |= existing
                continue
            device = record._ke_etims_device()
            vals = {
                "res_model": record._name,
                "res_id": record.id,
                "kind": kind,
                "company_id": record.company_id.id,
                "device_id": device.id,
                **values,
            }
            if record._name == "account.move":
                vals["move_id"] = record.id
            created |= self.sudo().create(vals)
        return created

    # -------------------------------------------------------------- actions

    def action_send_now(self):
        for document in self:
            if document.state in ("to_send", "sending", "failed", "pending"):
                if document.state == "failed":
                    document.sudo().write({"state": "to_send", "attempts": 0,
                                           "next_try": False})
                if document.state == "pending":
                    document.sudo()._ke_etims_poll_one(timeout=INTERACTIVE_TIMEOUT)
                else:
                    document.sudo()._ke_etims_process_one(timeout=INTERACTIVE_TIMEOUT)
        return True

    def action_cancel(self):
        for document in self:
            if document.state == "sent":
                raise UserError(_("A signed document cannot be cancelled; issue a "
                                  "credit note instead."))
            if document.kra_invoice_no:
                raise UserError(_(
                    "%(name)s holds KRA invoice number %(number)s. Release the "
                    "number first, once you have checked in the eTIMS portal "
                    "that KRA never recorded it.",
                    name=document.display_name, number=document.kra_invoice_no))
            document.sudo().state = "cancelled"
        return True

    def action_release_number(self):
        """Give back a number KRA never recorded, so a stuck device can go on."""
        self._ke_etims_check_manager()
        for document in self.sudo():
            if document.state == "sent" or not document.kra_invoice_no:
                continue
            number = document.kra_invoice_no
            if not document.device_id._ke_etims_release_invoice_no(number):
                raise UserError(_(
                    "KRA invoice number %(number)s is no longer the last one "
                    "drawn on %(device)s, so it cannot be handed back without "
                    "leaving a gap.", number=number, device=document.device_id.display_name))
            document.write({"kra_invoice_no": False, "state": "failed",
                            "may_have_been_processed": False})
            document.message_post(body=_(
                "KRA invoice number %(number)s released by %(user)s.",
                number=number, user=self.env.user.name))
        return True

    def action_mark_signed(self):
        """Record a receipt by hand, from the eTIMS portal, for a sale KRA
        recorded but whose answer never arrived."""
        self._ke_etims_check_manager()
        for document in self.sudo():
            if document.state == "sent":
                continue
            if not (document.kra_invoice_no and document.signature and document.receipt_no):
                raise UserError(_(
                    "Enter the KRA invoice number, receipt number and receipt "
                    "signature from the eTIMS portal first."))
            document.write({"state": "sent", "last_error": False, "next_try": False,
                            "signed_at": document.signed_at or fields.Datetime.now()})
            if not document.qr_url:
                document.qr_url = document._ke_etims_default_qr_url()
            source = document._ke_etims_source()
            if source:
                source._ke_etims_on_signed(document)
            document.message_post(body=_("Receipt recorded by hand by %s.", self.env.user.name))
        return True

    def action_open_source(self):
        self.ensure_one()
        source = self._ke_etims_source()
        if not source:
            raise UserError(_("The record this document was made for no longer exists."))
        return {
            "type": "ir.actions.act_window",
            "res_model": source._name,
            "res_id": source.id,
            "view_mode": "form",
        }

    def _ke_etims_check_manager(self):
        if not self.env.user.has_group("x_ke_etims.group_ke_etims_manager"):
            raise UserError(_("Only an eTIMS manager can do this."))

    def _ke_etims_default_qr_url(self):
        from ..backends.kra_direct import receipt_qr_url
        device = self.device_id
        return receipt_qr_url(device.environment, device.tin, device.bhf_id, self.signature)

    # ---------------------------------------------------------------- queue

    @api.model
    def _cron_process_queue(self):
        self._ke_etims_process_queue()

    @api.model
    def _ke_etims_process_queue(self, devices=None, limit=200):
        """Send what is due, device by device, Kenyan companies only."""
        Device = self.env["ke.etims.device"]
        self._ke_etims_assign_devices()
        devices = devices if devices is not None else Device._ke_etims_devices()
        now = fields.Datetime.now()
        for device in devices.sudo():
            if device.company_id.account_fiscal_country_id.code != "KE" \
                    or device.state != "initialised":
                continue
            due = self.sudo().search([
                ("device_id", "=", device.id),
                "|",
                "&", ("state", "=", "to_send"),
                "|", ("next_try", "=", False), ("next_try", "<=", now),
                "&", ("state", "=", "sending"), ("write_date", "<", now - STALE_SENDING),
            ], limit=limit)
            # In number order: a held number must go before anything new.
            due = due.sorted(lambda d: (not d.kra_invoice_no, d.kra_invoice_no or 0, d.id))
            strict = REGISTRY.get(device.backend) and REGISTRY[device.backend].strict_sequence
            for document in due:
                document._ke_etims_process_one()
                document._ke_etims_commit()
                if strict and document.kra_invoice_no and document.state != "sent" \
                        and document._ke_etims_needs_number():
                    # Everything after it would be refused as out of order.
                    break
            pending = self.sudo().search([
                ("device_id", "=", device.id), ("state", "=", "pending"),
                "|", ("next_try", "=", False), ("next_try", "<=", now),
            ], limit=limit)
            for document in pending:
                document._ke_etims_poll_one()
                document._ke_etims_commit()

    @api.model
    def _ke_etims_assign_devices(self):
        """Documents queued before their company had a device get one now."""
        orphans = self.sudo().search([("device_id", "=", False),
                                      ("state", "in", ("to_send", "failed"))])
        for document in orphans:
            source = document._ke_etims_source()
            device = source._ke_etims_device() if source else None
            if device:
                document.device_id = device

    # --------------------------------------------------------------- sending

    def _ke_etims_process_one(self, timeout=None):
        """Send one document. Records the outcome on it; never raises for a
        failure of the other side."""
        self.ensure_one()
        document = self.sudo()
        if document.state not in ("to_send", "sending"):
            return
        if not document._ke_etims_lock():
            return
        if document.state == "sending" \
                and document.write_date > fields.Datetime.now() - STALE_SENDING:
            # Another worker committed "sending" and is waiting on the answer;
            # its row lock ended with that commit, so the state is the guard.
            return
        source = document._ke_etims_source()
        if not source:
            document.write({"state": "cancelled",
                            "last_error": _("The record no longer exists.")})
            return
        if document.company_id.account_fiscal_country_id.code != "KE":
            document.write({"state": "cancelled",
                            "last_error": _("Not a Kenyan company.")})
            return
        device = document.device_id or source._ke_etims_device()
        if not device or device.state != "initialised":
            document._ke_etims_wait(_(
                "%(company)s has no initialised eTIMS device for branch %(branch)s.",
                company=document.company_id.name,
                branch=document.company_id.ke_etims_bhf_id or "00"))
            return
        if document.device_id != device:
            document.device_id = device
        needs_number = document._ke_etims_needs_number()
        strict = REGISTRY[device.backend].strict_sequence and needs_number
        if strict:
            if not device._ke_etims_lock():
                return
            blocking = device._ke_etims_blocking_document()
            if blocking and blocking != document:
                document._ke_etims_wait(_(
                    "Waiting for %(name)s, which holds KRA invoice number %(number)s.",
                    name=blocking.display_name, number=blocking.kra_invoice_no))
                return

        errors = source._ke_etims_validate(document)
        if errors:
            document._ke_etims_fail("\n".join(errors), release=True)
            return

        vals = {"state": "sending", "attempts": document.attempts + 1}
        if needs_number and not document.kra_invoice_no:
            vals["kra_invoice_no"] = device._ke_etims_next_invoice_no()
        if not document.confirmed_at:
            vals["confirmed_at"] = fields.Datetime.now()
        document.write(vals)
        # The number and the attempt are on disk before anything leaves.
        document._ke_etims_commit()

        try:
            data = source._ke_etims_prepare(document)
            adapter = device._ke_etims_adapter(document=document, timeout=timeout)
            receipt = document._ke_etims_dispatch(adapter, data)
        except EtimsError as exc:
            document._ke_etims_handle_error(exc)
            return
        except UserError as exc:
            document._ke_etims_fail(str(exc), release=True)
            return
        except Exception as exc:  # a bug: assume the worst about the request
            _logger.exception("eTIMS: sending %s failed unexpectedly", document.display_name)
            document._ke_etims_handle_error(EtimsError(
                str(exc), retryable=True, may_have_been_processed=True))
            return
        document._ke_etims_store_payload(data)
        document._ke_etims_apply_receipt(receipt)

    def _ke_etims_dispatch(self, adapter, data):
        """Call the backend for this kind. Extended by the other eTIMS modules."""
        if self.kind == "sale":
            return adapter.sign_sale(data)
        if self.kind == "credit_note":
            return adapter.sign_credit_note(data)
        raise UserError(_("eTIMS does not know how to send a %s.", self.kind))

    def _ke_etims_store_payload(self, data):
        totals = getattr(data, "totals", None)
        if not callable(totals):
            return
        totals = totals()
        self.totals_json = json.dumps({
            letter: {"taxable": str(totals.taxable[letter]), "tax": str(totals.tax[letter]),
                     "rate": str(totals.rates[letter])}
            for letter in totals.taxable
        })

    def _ke_etims_poll_one(self, timeout=None):
        """Ask the provider again about a sale it accepted but had not signed."""
        self.ensure_one()
        document = self.sudo()
        if document.state != "pending" or not document._ke_etims_lock():
            return
        if not document.provider_ref:
            document._ke_etims_fail(_("The provider gave no reference to check on."))
            return
        try:
            adapter = document.device_id._ke_etims_adapter(document=document, timeout=timeout)
            receipt = adapter.get_status(document.provider_ref, document.kra_invoice_no)
        except NotSupported as exc:
            document.write({"last_error": str(exc), "next_try": False})
            return
        except EtimsError as exc:
            document.write({
                "last_error": str(exc),
                "attempts": document.attempts + 1,
                "next_try": document._ke_etims_backoff(document.attempts + 1),
            })
            return
        document._ke_etims_apply_receipt(receipt)

    def _ke_etims_refetch(self):
        """Re-read this document's sale from the provider, e.g. after a
        callback. The callback itself is never believed."""
        self.ensure_one()
        if self.state in ("pending", "sending", "to_send") and self.provider_ref:
            self.sudo().write({"state": "pending"})
            self._ke_etims_poll_one()

    @api.model
    def _ke_etims_handle_callback(self, device, payload):
        """A provider says a record changed. Find ours and ask the provider."""
        adapter = device._ke_etims_adapter()
        event = adapter.handle_callback(payload)
        if event["kind"] == "sale":
            document = self.sudo().search([("device_id", "=", device.id),
                                           ("provider_ref", "=", event["provider_ref"])],
                                          limit=1)
            if document:
                document._ke_etims_refetch()
            return document
        registration = self.env["ke.etims.item.registration"].sudo().search([
            ("device_id", "=", device.id),
            ("provider_item_id", "=", event["provider_ref"])], limit=1)
        if registration and hasattr(adapter, "fetch_item"):
            item = adapter.fetch_item(event["provider_ref"])
            status = str(item.get("status") or item.get("sync_status") or "").upper()
            if status in ("FAILED", "REJECTED", "ERROR"):
                registration.write({"state": "failed",
                                    "last_error": item.get("error") or item.get("message")
                                    or status})
            elif status:
                registration.write({"state": "registered", "last_error": False})
        return registration

    # -------------------------------------------------------------- outcomes

    def _ke_etims_apply_receipt(self, receipt):
        self.ensure_one()
        vals = {
            "receipt_no": receipt.receipt_no or self.receipt_no,
            "total_receipt_no": receipt.total_receipt_no or self.total_receipt_no,
            "internal_data": receipt.internal_data or self.internal_data,
            "signature": receipt.signature or self.signature,
            "scu_id": receipt.scu_id or self.scu_id or self.device_id.scu_id,
            "mrc_no": receipt.mrc_no or self.mrc_no or self.device_id.mrc_no,
            "qr_url": receipt.qr_url or self.qr_url,
            "provider_ref": receipt.provider_ref or self.provider_ref,
            "may_have_been_processed": False,
        }
        if receipt.kra_invoice_no:
            vals["kra_invoice_no"] = receipt.kra_invoice_no
        if receipt.status == "signed":
            vals.update({
                "state": "sent",
                "signed_at": receipt.signed_at or fields.Datetime.now(),
                "signed_environment": self.device_id.environment,
                "last_error": False,
                "last_error_code": False,
                "next_try": False,
            })
            self.write(vals)
            source = self._ke_etims_source()
            if source:
                source._ke_etims_on_signed(self)
        elif receipt.status == "pending":
            vals.update({
                "state": "pending",
                "last_error": receipt.error or False,
                "next_try": self._ke_etims_backoff(max(self.attempts, 1), base=30),
            })
            self.write(vals)
        else:
            self.write(vals)
            self._ke_etims_fail(receipt.error or _("The provider could not sign it."),
                                release=not REGISTRY[self.device_id.backend].strict_sequence)

    def _ke_etims_handle_error(self, exc):
        self.ensure_one()
        if exc.duplicate:
            message = _(
                "%(error)s\n\nThe other side already has KRA invoice %(number)s, so "
                "the first attempt reached it but its answer was lost. Look the "
                "receipt up in the eTIMS portal and record it here with "
                "\"Record receipt\".", error=str(exc), number=self.kra_invoice_no)
            if self.provider_ref and REGISTRY[self.device_id.backend].supports("get_status"):
                self.write({"state": "pending", "last_error": str(exc), "next_try": False})
                return
            self._ke_etims_fail(message, code=exc.code, keep_number=True)
            return
        if exc.retryable or exc.may_have_been_processed:
            if self.attempts >= MAX_ATTEMPTS:
                processed = exc.may_have_been_processed or self.may_have_been_processed
                self._ke_etims_fail(
                    _("%(error)s\n\nGave up after %(count)s attempts.",
                      error=str(exc), count=self.attempts),
                    code=exc.code, release=not processed, keep_number=processed)
                return
            self.write({
                "state": "to_send",
                "last_error": str(exc),
                "last_error_code": exc.code or False,
                "may_have_been_processed": exc.may_have_been_processed,
                "next_try": self._ke_etims_backoff(self.attempts),
            })
            return
        self._ke_etims_fail(str(exc), code=exc.code, release=True)

    def _ke_etims_backoff(self, attempts, base=BASE_DELAY):
        delay = min(base * 2 ** max(attempts - 1, 0), MAX_DELAY)
        return fields.Datetime.now() + timedelta(seconds=delay * random.uniform(0.5, 1.5))

    def _ke_etims_wait(self, reason):
        """Not an attempt: something local has to happen first."""
        self.write({"state": "to_send", "last_error": reason,
                    "next_try": self._ke_etims_backoff(1)})

    def _ke_etims_fail(self, message, *, code=None, release=False, keep_number=False):
        self.ensure_one()
        vals = {"state": "failed", "last_error": message,
                "last_error_code": code or False, "next_try": False}
        if self.kra_invoice_no and release and not keep_number \
                and not self.may_have_been_processed:
            if self.device_id._ke_etims_release_invoice_no(self.kra_invoice_no):
                vals["kra_invoice_no"] = False
        if keep_number:
            vals["may_have_been_processed"] = True
        self.write(vals)
        source = self._ke_etims_source()
        if source:
            source._ke_etims_on_failed(self)
