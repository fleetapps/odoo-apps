# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The M-Pesa transaction log: every STK request and every receipt.

A receipt number exists once per company, whatever told us about it first:
the C2B confirmation, the STK callback, the Pull API, a statement file or a
cashier typing it in. Every path ends in :meth:`_upsert_receipt` (or, for
STK, :meth:`_stk_apply_result`), under a transaction-scoped advisory lock on
the receipt number, so two callbacks processed at the same instant by two
workers still produce one record and one statement line.
"""

import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from ..lib import daraja

_logger = logging.getLogger(__name__)

STATES = [
    ("draft", "Draft"),
    ("requested", "Waiting for the customer"),
    ("unknown", "Outcome unknown"),
    ("manual", "Entered by hand, not confirmed"),
    ("confirmed", "Confirmed"),
    ("failed", "Failed"),
]
SOURCES = [
    ("stk", "STK Push"),
    ("c2b", "C2B confirmation"),
    ("pull", "Pull API"),
    ("statement", "Statement import"),
    ("manual", "Manual entry"),
    ("status", "Transaction Status"),
]
CHANNELS = [
    ("stk", "STK Push"),
    ("till", "Paid to till"),
    ("paybill", "Paybill"),
    ("manual", "Manual"),
]
EXCEPTIONS = [
    ("none", "None"),
    ("unconfirmed_manual", "Manual receipt not confirmed by M-PESA"),
    ("cross_till", "Paid to another till"),
    ("amount_mismatch", "Amount differs"),
    ("reused", "Receipt used twice"),
    ("double_claim", "Claimed twice"),
    ("unverified_callback", "Callback not confirmed by STK Query"),
    ("no_journal", "Shortcode has no journal"),
]
ALLOCATION_STATES = [
    ("pending", "Not in the bank yet"),
    ("unallocated", "Unallocated"),
    ("claimed", "Claimed, settles at close"),
    ("allocated", "Allocated"),
    ("parked", "Parked in suspense"),
]
RECEIPT_RE = re.compile(r"^[A-Z0-9]{8,12}$")
# "Pay Bill" (C2B), "CustomerPayBillOnline" (STK), "c2b-pay-bill-debit" (Pull)
PAYBILL_RE = re.compile(r"pay[\s-]?bill", re.I)
clean_receipt = daraja.clean_receipt


class MpesaTransaction(models.Model):
    _name = "mpesa.transaction"
    _description = "M-Pesa Transaction"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "trans_time desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True, index="trigram")
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company)
    shortcode_id = fields.Many2one(
        "mpesa.shortcode", required=True, index=True, check_company=True,
        ondelete="restrict", tracking=True)
    journal_id = fields.Many2one(related="shortcode_id.journal_id", store=True)
    analytic_account_id = fields.Many2one(
        related="shortcode_id.analytic_account_id", store=True, string="Outlet")
    currency_id = fields.Many2one(related="company_id.currency_id")
    source = fields.Selection(SOURCES, required=True, default="c2b", readonly=True)
    channel = fields.Selection(CHANNELS, compute="_compute_channel", store=True)
    state = fields.Selection(STATES, required=True, default="draft", tracking=True,
                             index=True)

    receipt = fields.Char(index=True, copy=False, tracking=True)
    checkout_request_id = fields.Char(index=True, copy=False, readonly=True)
    merchant_request_id = fields.Char(copy=False, readonly=True)
    idempotency_key = fields.Char(index=True, copy=False, readonly=True)

    amount = fields.Monetary(tracking=True)
    amount_requested = fields.Monetary(readonly=True)
    trans_time = fields.Datetime("Paid at", index=True)
    trans_date = fields.Date("Date", compute="_compute_trans_date", store=True, index=True)
    org_balance = fields.Monetary("M-PESA balance after", readonly=True)

    phone = fields.Char(copy=False, help="Full number, STK Push only; masked "
                                         "after the retention period.")
    msisdn_masked = fields.Char("Customer number", copy=False)
    customer_name = fields.Char(copy=False)
    partner_id = fields.Many2one("res.partner", check_company=True, index=True)
    bill_ref = fields.Char("Account number", help="BillRefNumber: what a paybill "
                                                  "customer typed as account number.")
    invoice_number = fields.Char(readonly=True)
    third_party_trans_id = fields.Char(readonly=True)
    transaction_type = fields.Char(readonly=True)
    business_shortcode = fields.Char("Paid to", readonly=True,
                                     help="BusinessShortCode as M-PESA reported it.")

    account_reference = fields.Char(readonly=True)
    description = fields.Char(readonly=True)
    result_code = fields.Char(readonly=True)
    result_desc = fields.Char(readonly=True)
    cashier_message = fields.Char(readonly=True)
    requested_at = fields.Datetime(readonly=True)
    confirmed_at = fields.Datetime(readonly=True)
    query_count = fields.Integer(readonly=True)
    last_query_at = fields.Datetime(readonly=True)
    awaiting_receipt = fields.Boolean(
        readonly=True, help="STK Query says paid, but STK Query carries no receipt "
                            "number: it is attached when the C2B confirmation or "
                            "Pull row for the same till and amount arrives.")
    pending_callback = fields.Json(readonly=True, copy=False,
                                   help="A successful callback held back until STK "
                                        "Query confirms it.")

    res_model = fields.Char("Document model", readonly=True, index=True)
    res_id = fields.Many2oneReference("Document", model_field="res_model", readonly=True)
    move_id = fields.Many2one("account.move", "Invoice", check_company=True, index=True,
                              readonly=True)
    user_id = fields.Many2one("res.users", "Requested by", readonly=True,
                              default=lambda self: self.env.user)

    statement_line_id = fields.Many2one(
        "account.bank.statement.line", copy=False, index=True, readonly=True,
        ondelete="set null")
    is_reconciled = fields.Boolean(related="statement_line_id.is_reconciled", store=True)
    allocation_state = fields.Selection(
        ALLOCATION_STATES, compute="_compute_allocation_state", store=True, index=True)
    claimed = fields.Boolean(readonly=True, copy=False,
                             help="Picked by a document that settles it later "
                                  "(a POS session at closing).")
    claim_ref = fields.Char("Claimed by", readonly=True, copy=False)
    park_reason = fields.Char(copy=False, tracking=True)

    exception = fields.Selection(EXCEPTIONS, required=True, default="none",
                                 tracking=True, index=True)
    exception_note = fields.Text()
    manual_user_id = fields.Many2one("res.users", "Entered by", readonly=True)
    manual_entered_at = fields.Datetime("Entered at", readonly=True)
    alerted_at = fields.Datetime(readonly=True, copy=False)
    callback_ids = fields.One2many("mpesa.callback", "transaction_id")
    seconds_to_confirm = fields.Integer(compute="_compute_seconds_to_confirm", store=True)

    _receipt_uniq = models.UniqueIndex(
        "(company_id, receipt) WHERE receipt IS NOT NULL",
        "This M-PESA receipt is already recorded for this company.",
    )
    _checkout_uniq = models.UniqueIndex(
        "(checkout_request_id) WHERE checkout_request_id IS NOT NULL",
        "This STK request is already recorded.",
    )
    _idempotency_uniq = models.UniqueIndex(
        "(company_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
        "This payment request was already sent.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("receipt", "amount", "source", "state", "msisdn_masked", "phone")
    def _compute_name(self):
        for tx in self:
            if tx.receipt:
                tx.name = tx.receipt
            else:
                who = tx.msisdn_masked or daraja.mask_msisdn(tx.phone) or ""
                tx.name = _("STK %(amount)s %(who)s", amount=int(tx.amount or 0),
                            who=who).strip()

    @api.depends("source", "shortcode_id.kind", "transaction_type")
    def _compute_channel(self):
        for tx in self:
            if tx.source == "stk":
                tx.channel = "stk"
            elif tx.source == "manual":
                tx.channel = "manual"
            elif tx.shortcode_id.kind == "paybill" or PAYBILL_RE.search(tx.transaction_type or ""):
                tx.channel = "paybill"
            else:
                tx.channel = "till"

    @api.depends("trans_time")
    def _compute_trans_date(self):
        for tx in self:
            if tx.trans_time:
                moment = fields.Datetime.to_datetime(tx.trans_time)
                tx.trans_date = (moment.replace(tzinfo=daraja.UTC)
                                 .astimezone(daraja.NAIROBI).date())
            else:
                tx.trans_date = False

    @api.depends("state", "statement_line_id", "is_reconciled", "park_reason", "claimed")
    def _compute_allocation_state(self):
        for tx in self:
            if tx.state not in ("confirmed", "manual"):
                tx.allocation_state = False
            elif not tx.statement_line_id:
                tx.allocation_state = "claimed" if tx.claimed else "pending"
            elif tx.is_reconciled:
                tx.allocation_state = "allocated"
            elif tx.park_reason:
                tx.allocation_state = "parked"
            elif tx.claimed:
                tx.allocation_state = "claimed"
            else:
                tx.allocation_state = "unallocated"

    @api.depends("requested_at", "confirmed_at", "source")
    def _compute_seconds_to_confirm(self):
        for tx in self:
            if tx.source == "stk" and tx.requested_at and tx.confirmed_at:
                tx.seconds_to_confirm = int(
                    (tx.confirmed_at - tx.requested_at).total_seconds())
            else:
                tx.seconds_to_confirm = 0

    @api.constrains("receipt")
    def _check_receipt(self):
        for tx in self:
            if tx.receipt and not RECEIPT_RE.match(tx.receipt):
                raise ValidationError(_("%r is not an M-PESA receipt number.", tx.receipt))

    # ------------------------------------------------------------------
    # Locks
    # ------------------------------------------------------------------

    def _lock(self, *parts):
        """Serialise work on one receipt or STK request across workers.

        Transaction-scoped: released at commit or rollback.
        """
        key = "mpesa:" + ":".join(str(part) for part in parts)
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [key])

    # ------------------------------------------------------------------
    # STK Push
    # ------------------------------------------------------------------

    @api.model
    def _stk_request(self, shortcode, amount, phone, *, reference, description=None,
                     origin=None, idempotency_key=None, partner=None, extra_vals=None):
        """Prompt ``phone`` for ``amount`` on ``shortcode`` and return the transaction.

        With an ``idempotency_key`` an open request for the same key (a double
        click, a POS retry, a lost answer) is returned instead of prompting the
        customer a second time.
        """
        shortcode.ensure_one()
        if not shortcode.stk_enabled:
            raise UserError(_("STK Push is off for %s.", shortcode.display_name))
        try:
            amount_int = daraja.whole_shillings(amount)
            msisdn = daraja.normalize_msisdn(phone)
        except ValueError as exc:
            raise UserError(_("%s", exc)) from exc
        if amount_int < 1:
            raise UserError(_("M-PESA cannot take less than KES 1."))
        company = shortcode.company_id
        if idempotency_key:
            self._lock("idem", company.id, idempotency_key)
            existing = self.sudo().search([("company_id", "=", company.id),
                                           ("idempotency_key", "=", idempotency_key)], limit=1)
            if existing:
                recent = existing.create_date > fields.Datetime.now() - timedelta(minutes=2)
                if existing.state == "confirmed" or (
                        existing.state in ("draft", "requested", "unknown") and recent):
                    return existing
                # Failed, or abandoned long enough ago: free the key for a new try.
                existing.idempotency_key = f"{idempotency_key}#{existing.id}"
                existing.flush_recordset(["idempotency_key"])
        vals = {
            "company_id": company.id,
            "shortcode_id": shortcode.id,
            "source": "stk",
            "state": "draft",
            "amount": amount_int,
            "amount_requested": amount_int,
            "phone": msisdn,
            "msisdn_masked": daraja.mask_msisdn(msisdn),
            "account_reference": daraja.account_reference(reference),
            "description": daraja.transaction_desc(description or reference),
            "idempotency_key": idempotency_key,
            "partner_id": partner.id if partner else False,
            "user_id": self.env.uid,
        }
        if origin:
            vals.update(res_model=origin._name, res_id=origin.id)
            if origin._name == "account.move":
                vals["move_id"] = origin.id
        vals.update(extra_vals or {})
        tx = self.sudo().create(vals)
        tx._stk_send()
        return tx

    def _stk_send(self):
        self.ensure_one()
        shortcode = self.shortcode_id.sudo()
        passkey = shortcode._passkey()
        if not passkey:
            self._stk_fail("", _("%s has no passkey.", shortcode.display_name))
            return
        try:
            answer = shortcode._client().stk_push(
                business_shortcode=shortcode.stk_business_shortcode or shortcode.number,
                passkey=passkey,
                amount=self.amount,
                phone=self.phone,
                party_b=shortcode.stk_party_b or shortcode.number,
                transaction_type=shortcode.stk_transaction_type,
                callback_url=shortcode._callback_url("stk"),
                reference=self.account_reference,
                description=self.description,
            )
        except daraja.DarajaTransportError as exc:
            if not exc.sent:
                self._stk_fail("", _("M-PESA could not be reached, nothing was sent. "
                                     "Try again."))
            else:
                # Never retried: Safaricom may have prompted the customer. A
                # payment that does happen arrives as a callback or C2B
                # confirmation and is linked back by phone and amount.
                self.write({
                    "state": "unknown",
                    "requested_at": fields.Datetime.now(),
                    "cashier_message": _(
                        "M-PESA did not answer. The customer may still get the "
                        "prompt: wait for their SMS before trying again."),
                })
            return
        except daraja.DarajaError as exc:
            if exc.subscriber_locked:
                message = daraja.stk_message("1001")
            elif exc.busy:
                message = _("M-PESA is busy. Try again in a minute.")
            else:
                message = _("M-PESA refused the prompt: %s", exc.message)
            self._stk_fail(exc.code or "", message, desc=exc.message)
            return
        except (UserError, ValueError) as exc:
            self._stk_fail("", str(exc))
            return
        self._stk_save_ids(answer.get("MerchantRequestID"), answer.get("CheckoutRequestID"))

    def _stk_fail(self, code, message, desc=None):
        self.write({
            "state": "failed",
            "result_code": code or False,
            "result_desc": desc or message,
            "cashier_message": message,
            "requested_at": self.requested_at or fields.Datetime.now(),
        })
        self._notify_origin()

    def _stk_save_ids(self, merchant_request_id, checkout_request_id):
        """Store Daraja's ids; take over the callback if it got here first."""
        self.ensure_one()
        if not checkout_request_id:
            self._stk_fail("", _("M-PESA accepted the prompt but returned no request id."))
            return
        self._lock("stk", checkout_request_id)
        # Only a placeholder an early callback left: never an older request
        # (a placeholder was never sent, so it has no request time).
        placeholder = self.sudo().search(
            [("checkout_request_id", "=", checkout_request_id), ("id", "!=", self.id),
             ("requested_at", "=", False)], limit=1)
        outcome = placeholder._stk_outcome() if placeholder else None
        if placeholder:
            placeholder._hand_over_to(self)
        self.write({
            "merchant_request_id": merchant_request_id,
            "checkout_request_id": checkout_request_id,
            "state": "requested",
            "requested_at": fields.Datetime.now(),
            "cashier_message": _("Prompt sent. Waiting for the customer's PIN."),
        })
        if outcome:
            self._stk_apply_result(*outcome)

    def _stk_outcome(self):
        """What a callback placeholder learned, to replay on the real request."""
        self.ensure_one()
        metadata = {
            "Amount": self.amount,
            "MpesaReceiptNumber": self.receipt,
            "TransactionDate": daraja.daraja_timestamp(self.trans_time) if self.trans_time else None,
            "PhoneNumber": self.phone,
        }
        return self.result_code, self.result_desc, metadata

    def _hand_over_to(self, target):
        """Move what this placeholder holds onto ``target`` and delete it."""
        self.ensure_one()
        self.callback_ids.write({"transaction_id": target.id})
        line = self.statement_line_id
        self.write({"statement_line_id": False, "receipt": False,
                    "checkout_request_id": False})
        if line:
            line.mpesa_transaction_id = target
            target.statement_line_id = line
        self.unlink()

    @api.model
    def _stk_from_callback(self, shortcode, body):
        """Apply an STK callback body; upsert on CheckoutRequestID."""
        callback = (body.get("Body") or {}).get("stkCallback") or {}
        checkout_id = callback.get("CheckoutRequestID")
        if not checkout_id:
            raise ValidationError(_("An STK callback without CheckoutRequestID."))
        self._lock("stk", checkout_id)
        tx = self.sudo().search([("checkout_request_id", "=", checkout_id)],
                                order="id desc", limit=1)
        code = str(callback.get("ResultCode", ""))
        metadata = daraja.stk_metadata(callback)
        if not tx:
            # Our own save has not landed yet (or the request was lost to a
            # timeout). Keep the outcome on a placeholder that the requester's
            # save merges into, or that stands on its own as a receipt.
            tx = self.sudo().create({
                "company_id": shortcode.company_id.id,
                "shortcode_id": shortcode.id,
                "source": "stk",
                "state": "requested",
                "checkout_request_id": checkout_id,
                "merchant_request_id": callback.get("MerchantRequestID"),
                "amount": daraja.parse_amount(metadata.get("Amount")),
                "user_id": False,
            })
            orphan = tx._stk_find_unknown(metadata)
            if orphan:
                tx._hand_over_to(orphan)
                orphan.write({"checkout_request_id": checkout_id,
                              "merchant_request_id": callback.get("MerchantRequestID"),
                              "state": "requested"})
                tx = orphan
        tx._stk_apply_result(code, callback.get("ResultDesc"), metadata, from_callback=True)
        return tx

    def _stk_find_unknown(self, metadata):
        """An "outcome unknown" request this callback belongs to: same phone,
        same amount, same till, within five minutes."""
        phone = metadata.get("PhoneNumber")
        try:
            phone = daraja.normalize_msisdn(phone) if phone else None
        except ValueError:
            phone = None
        amount = daraja.parse_amount(metadata.get("Amount"))
        if not phone or not amount:
            return self.browse()
        since = fields.Datetime.now() - timedelta(minutes=5)
        candidates = self.sudo().search([
            ("shortcode_id", "=", self.shortcode_id.id),
            ("state", "=", "unknown"),
            ("phone", "=", phone),
            ("amount", "=", amount),
            ("requested_at", ">=", since),
            ("checkout_request_id", "=", False),
        ])
        return candidates if len(candidates) == 1 else self.browse()

    def _stk_apply_result(self, result_code, result_desc, metadata, from_callback=False):
        self.ensure_one()
        code = str(result_code if result_code is not None else "")
        if self.state in ("confirmed", "failed") and self.result_code == code:
            return  # duplicate delivery
        if code == "":
            return
        if code != "0":
            if self.state == "confirmed":
                # A late failure after a confirmed receipt is noise; keep the money.
                return
            self._stk_fail(code, daraja.stk_message(code, result_desc), desc=result_desc)
            return
        amount = daraja.parse_amount(metadata.get("Amount")) or self.amount
        threshold = self.shortcode_id.stk_confirm_threshold
        if from_callback and threshold and amount > threshold and not self.pending_callback:
            # Callbacks are not signed: above the threshold, believe STK Query.
            self.write({
                "pending_callback": {"code": code, "desc": result_desc, "metadata": metadata},
                "cashier_message": _("Payment reported; confirming with M-PESA."),
            })
            if self.checkout_request_id:
                self.with_delay(priority=5)._stk_query()
            return
        self._stk_confirm(result_desc, metadata)

    def _stk_confirm(self, result_desc, metadata):
        self.ensure_one()
        receipt = clean_receipt(metadata.get("MpesaReceiptNumber"))
        amount = daraja.parse_amount(metadata.get("Amount")) or self.amount
        when = daraja.parse_daraja_time(metadata.get("TransactionDate"))
        phone = metadata.get("PhoneNumber")
        vals = {
            "state": "confirmed",
            "result_code": "0",
            "result_desc": result_desc or _("The service request is processed successfully."),
            "cashier_message": daraja.stk_message("0"),
            "confirmed_at": fields.Datetime.now(),
            "pending_callback": False,
            "amount": amount,
        }
        if when:
            vals["trans_time"] = when.replace(tzinfo=None)
        elif not self.trans_time:
            vals["trans_time"] = fields.Datetime.now()
        if phone:
            try:
                msisdn = daraja.normalize_msisdn(phone)
                vals.update(phone=msisdn, msisdn_masked=daraja.mask_msisdn(msisdn))
            except ValueError:
                pass
        if self.amount_requested and float_compare(
                amount, self.amount_requested, precision_digits=2) != 0:
            vals.update(exception="amount_mismatch", exception_note=_(
                "Asked for %(asked)s, M-PESA reports %(paid)s.",
                asked=self.amount_requested, paid=amount))
        if receipt:
            self._lock("receipt", self.company_id.id, receipt)
            other = self.sudo().search([("company_id", "=", self.company_id.id),
                                        ("receipt", "=", receipt), ("id", "!=", self.id)],
                                       limit=1)
            if other:
                vals.update(self._absorb(other))
            vals["receipt"] = receipt
            vals["awaiting_receipt"] = False
        else:
            vals["awaiting_receipt"] = True
        self.write(vals)
        self._after_confirm()

    def _absorb(self, other):
        """Take over a receipt ``other`` recorded first (usually its C2B
        confirmation) and delete it. Returns values for ``self``."""
        vals = {}
        for fname in ("msisdn_masked", "customer_name", "bill_ref", "invoice_number",
                      "third_party_trans_id", "transaction_type", "business_shortcode",
                      "org_balance", "trans_time"):
            if other[fname] and not self[fname]:
                vals[fname] = other[fname]
        if other.claimed:
            vals.update(exception="double_claim", exception_note=_(
                "Receipt %(receipt)s was claimed by %(claim)s and also paid this "
                "STK request.", receipt=other.receipt, claim=other.claim_ref))
        if other.shortcode_id != self.shortcode_id and \
                other.shortcode_id not in self.shortcode_id._family():
            vals.update(exception="cross_till", exception_note=_(
                "M-PESA reported receipt %(receipt)s on %(other)s.",
                receipt=other.receipt, other=other.shortcode_id.display_name))
        other._hand_over_to(self)
        return vals

    def _stk_query(self):
        """Ask M-PESA how an STK request ended. Its answer is final."""
        for tx in self.filtered(lambda t: t.checkout_request_id and t.source == "stk"):
            if tx.state not in ("requested",) and not tx.pending_callback:
                continue
            shortcode = tx.shortcode_id.sudo()
            try:
                answer = shortcode._client().stk_query(
                    business_shortcode=shortcode.stk_business_shortcode or shortcode.number,
                    passkey=shortcode._passkey(),
                    checkout_request_id=tx.checkout_request_id,
                )
            except (daraja.DarajaError, daraja.DarajaTransportError) as exc:
                _logger.info("STK Query for %s failed: %s", tx.checkout_request_id, exc)
                tx.write({"query_count": tx.query_count + 1,
                          "last_query_at": fields.Datetime.now()})
                continue
            tx.write({"query_count": tx.query_count + 1, "last_query_at": fields.Datetime.now()})
            if answer.get("pending"):
                continue
            tx._lock("stk", tx.checkout_request_id)
            code = str(answer.get("ResultCode", ""))
            held = tx.pending_callback
            if code == "0":
                metadata = (held or {}).get("metadata") or {}
                tx._stk_confirm(answer.get("ResultDesc"), metadata)
            elif held:
                tx.write({"exception": "unverified_callback", "exception_note": _(
                    "A callback said paid, STK Query says %(code)s: %(desc)s.",
                    code=code, desc=answer.get("ResultDesc"))})
                tx._stk_fail(code, daraja.stk_message(code, answer.get("ResultDesc")),
                             desc=answer.get("ResultDesc"))
            elif code:
                tx._stk_fail(code, daraja.stk_message(code, answer.get("ResultDesc")),
                             desc=answer.get("ResultDesc"))

    def action_stk_query(self):
        self._stk_query()
        return True

    @api.model
    def _cron_stk_followup(self):
        """STK requests without a callback past their shortcode's delay: ask."""
        now = fields.Datetime.now()
        candidates = self.sudo().search([
            ("source", "=", "stk"),
            ("state", "=", "requested"),
            ("checkout_request_id", "!=", False),
            ("query_count", "<", 6),
        ])
        for tx in candidates:
            delay = timedelta(seconds=tx.shortcode_id.stk_query_delay or 90)
            started = tx.requested_at or tx.create_date
            if started + delay > now:
                continue
            if tx.last_query_at and tx.last_query_at + timedelta(seconds=30) > now:
                continue
            tx.with_delay(priority=5, identity_key=f"stk-query-{tx.id}-{tx.query_count}")._stk_query()
        # Requests M-PESA never settled: stop waiting after 15 minutes.
        stale = self.sudo().search([
            ("source", "=", "stk"), ("state", "=", "requested"),
            ("requested_at", "<", now - timedelta(minutes=15)), ("query_count", ">=", 6),
        ])
        for tx in stale:
            tx.write({"state": "unknown", "cashier_message": _(
                "M-PESA never settled this prompt. If the customer has an SMS, "
                "the receipt will show up as a till payment.")})

    # ------------------------------------------------------------------
    # Receipts: C2B, Pull, statements, Transaction Status
    # ------------------------------------------------------------------

    @api.model
    def _from_c2b(self, registration, body):
        """A C2B confirmation body, as posted to ``registration``'s URL."""
        receipt = clean_receipt(body.get("TransID"))
        if not receipt:
            raise ValidationError(_("A C2B confirmation without TransID."))
        shortcode = registration._receiving_shortcode(body.get("BusinessShortCode"))
        names = " ".join(" ".join(str(body.get(key) or "") for key in
                                  ("FirstName", "MiddleName", "LastName")).split())
        trans_time = daraja.parse_daraja_time(body.get("TransTime"))
        vals = {
            "amount": daraja.parse_amount(body.get("TransAmount")),
            "trans_time": trans_time.replace(tzinfo=None) if trans_time else fields.Datetime.now(),
            "msisdn_masked": (body.get("MSISDN") or "").replace(" ", "") or False,
            "customer_name": names or False,
            "bill_ref": (body.get("BillRefNumber") or "").strip() or False,
            "invoice_number": (body.get("InvoiceNumber") or "").strip() or False,
            "third_party_trans_id": (body.get("ThirdPartyTransID") or "").strip() or False,
            "transaction_type": body.get("TransactionType") or False,
            "business_shortcode": str(body.get("BusinessShortCode") or "") or False,
        }
        balance = body.get("OrgAccountBalance")
        if balance not in (None, ""):
            vals["org_balance"] = daraja.parse_amount(balance)
        tx = self._upsert_receipt(shortcode, receipt, vals, "c2b")
        stamp = {"last_callback_at": fields.Datetime.now()}
        if balance not in (None, ""):
            stamp.update(last_org_balance=vals["org_balance"],
                         last_org_balance_at=vals["trans_time"])
        (shortcode | registration).sudo().write(stamp)
        return tx

    @api.model
    def _upsert_receipt(self, shortcode, receipt, vals, source):
        """The one way a receipt enters the log. Returns its transaction."""
        receipt = clean_receipt(receipt)
        company = shortcode.company_id
        self._lock("receipt", company.id, receipt)
        Tx = self.sudo()
        tx = Tx.search([("company_id", "=", company.id), ("receipt", "=", receipt)], limit=1)
        if tx:
            tx._confirm_known(shortcode, vals, source)
            return tx
        stk = Tx._stk_awaiting(shortcode, vals.get("amount"), vals.get("trans_time"))
        if stk:
            stk.write(dict({k: v for k, v in vals.items() if v and not stk[k]},
                           receipt=receipt, awaiting_receipt=False))
            stk._after_confirm()
            return stk
        tx = Tx.create(dict(vals, company_id=company.id, shortcode_id=shortcode.id,
                            receipt=receipt, source=source, state="confirmed",
                            confirmed_at=fields.Datetime.now(), user_id=False))
        tx._after_confirm()
        return tx

    def _confirm_known(self, shortcode, vals, source):
        """A receipt we already hold, told to us again (possibly by another path)."""
        self.ensure_one()
        updates = {k: v for k, v in vals.items() if v and not self[k] and k != "amount"}
        amount = vals.get("amount")
        same_till = shortcode == self.shortcode_id or shortcode in self.shortcode_id._family()
        amount_ok = not amount or float_compare(amount, self.amount, precision_digits=2) == 0
        if self.state == "manual":
            if amount_ok and same_till:
                updates.update(state="confirmed", confirmed_at=fields.Datetime.now(),
                               exception="none", exception_note=False)
                if not self.trans_time and vals.get("trans_time"):
                    updates["trans_time"] = vals["trans_time"]
                self.message_post(body=_("Confirmed by M-PESA (%s).",
                                         dict(SOURCES).get(source, source)))
            elif not same_till:
                updates.update(state="confirmed", confirmed_at=fields.Datetime.now(),
                               shortcode_id=shortcode.id, amount=amount or self.amount,
                               exception="cross_till", exception_note=_(
                                   "Entered by hand on %(claimed)s; M-PESA says it was "
                                   "paid to %(paid)s.", claimed=self.shortcode_id.display_name,
                                   paid=shortcode.display_name))
            else:
                updates.update(state="confirmed", confirmed_at=fields.Datetime.now(),
                               amount=amount, exception="amount_mismatch", exception_note=_(
                                   "Entered by hand as %(typed)s; M-PESA says %(paid)s.",
                                   typed=self.amount, paid=amount))
        else:
            if not same_till and self.exception == "none":
                updates.update(exception="cross_till", exception_note=_(
                    "Recorded on %(here)s, reported by M-PESA on %(there)s.",
                    here=self.shortcode_id.display_name, there=shortcode.display_name))
            elif not amount_ok and self.exception == "none":
                updates.update(exception="amount_mismatch", exception_note=_(
                    "Recorded as %(here)s, reported by %(source)s as %(there)s.",
                    here=self.amount, source=dict(SOURCES).get(source, source), there=amount))
        was_manual = self.state == "manual"
        if updates:
            self.write(updates)
        if self.state == "confirmed":
            # Only a state change is news; a repeat of a known receipt is not.
            self._after_confirm(notify=was_manual)

    def _stk_awaiting(self, shortcode, amount, trans_time):
        """An STK request STK Query called paid, waiting for this receipt."""
        if not amount:
            return self.browse()
        domain = [("shortcode_id", "in", (shortcode | shortcode._family()).ids),
                  ("awaiting_receipt", "=", True), ("state", "=", "confirmed"),
                  ("amount", "=", amount)]
        if trans_time:
            domain += [("requested_at", ">=", trans_time - timedelta(minutes=10)),
                       ("requested_at", "<=", trans_time + timedelta(minutes=2))]
        candidates = self.search(domain)
        return candidates if len(candidates) == 1 else self.browse()

    def _after_confirm(self, notify=True):
        for tx in self.filtered(lambda t: t.state == "confirmed"):
            tx._ensure_statement_line()
            tx._auto_match()
            if notify:
                tx._notify_origin()

    # ------------------------------------------------------------------
    # Manual entry
    # ------------------------------------------------------------------

    @api.model
    def _manual_entry(self, shortcode, receipt, amount, extra_vals=None):
        """A receipt typed in by a cashier (Daraja down, customer shows an SMS).

        Stays flagged until M-PESA confirms the same receipt and amount on the
        same till. A receipt M-PESA has already confirmed is returned at once,
        provided it matches and nobody used it before.
        """
        receipt = clean_receipt(receipt)
        if not RECEIPT_RE.match(receipt):
            raise UserError(_("%r is not an M-PESA receipt number.", receipt))
        try:
            amount = daraja.whole_shillings(amount)
        except ValueError as exc:
            raise UserError(_("%s", exc)) from exc
        company = shortcode.company_id
        self._lock("receipt", company.id, receipt)
        existing = self.sudo().search([("company_id", "=", company.id),
                                       ("receipt", "=", receipt)], limit=1)
        if existing:
            if existing.state != "confirmed":
                raise UserError(_("Receipt %s was already entered.", receipt))
            if existing.claimed or existing.is_reconciled:
                raise UserError(_("Receipt %s has already been used.", receipt))
            if shortcode != existing.shortcode_id and \
                    shortcode not in existing.shortcode_id._family():
                raise UserError(_("Receipt %(receipt)s was paid to %(till)s, not this till.",
                                  receipt=receipt, till=existing.shortcode_id.display_name))
            if float_compare(existing.amount, amount, precision_digits=2) != 0:
                raise UserError(_("Receipt %(receipt)s is for %(amount)s.",
                                  receipt=receipt, amount=existing.amount))
            return existing
        vals = {
            "company_id": company.id,
            "shortcode_id": shortcode.id,
            "source": "manual",
            "state": "manual",
            "receipt": receipt,
            "amount": amount,
            "trans_time": fields.Datetime.now(),
            "manual_user_id": self.env.uid,
            "manual_entered_at": fields.Datetime.now(),
            "exception": "unconfirmed_manual",
            "exception_note": _("Waiting for M-PESA to confirm this receipt."),
            "user_id": self.env.uid,
        }
        vals.update(extra_vals or {})
        tx = self.sudo().create(vals)
        if company.mpesa_transaction_status_check:
            tx.with_delay(priority=10)._request_transaction_status()
        return tx

    @api.model
    def _cron_alert_unconfirmed_manual(self):
        """Manual receipts M-PESA has not confirmed in time: tell the supervisor."""
        now = fields.Datetime.now()
        for company in self.env["res.company"].sudo().search([]):
            minutes = company.mpesa_manual_alert_minutes or 30
            late = self.sudo().search([
                ("company_id", "=", company.id), ("state", "=", "manual"),
                ("alerted_at", "=", False),
                ("manual_entered_at", "<", now - timedelta(minutes=minutes)),
            ])
            for tx in late:
                tx._alert_supervisor(minutes)

    def _alert_supervisor(self, minutes):
        self.ensure_one()
        supervisor = self.shortcode_id.supervisor_id or self.shortcode_id.company_id.mpesa_supervisor_id
        summary = _("M-PESA has not confirmed receipt %(receipt)s (%(amount)s) after "
                    "%(minutes)s minutes", receipt=self.receipt, amount=self.amount,
                    minutes=minutes)
        note = _("Entered by %(user)s on %(till)s. Check the customer's SMS against "
                 "the till statement: a fake M-PESA message looks like this.",
                 user=self.manual_user_id.name or "?", till=self.shortcode_id.display_name)
        if supervisor:
            self.activity_schedule(
                "x_ke_mpesa.mail_activity_unconfirmed_receipt",
                user_id=supervisor.id, summary=summary, note=note)
            supervisor._bus_send("simple_notification", {
                "type": "danger", "sticky": True, "title": _("Unconfirmed M-PESA receipt"),
                "message": summary,
            })
        else:
            self.message_post(body=summary)
        self.alerted_at = fields.Datetime.now()

    # ------------------------------------------------------------------
    # Transaction Status (optional second opinion on a receipt)
    # ------------------------------------------------------------------

    def _request_transaction_status(self):
        for tx in self:
            shortcode = tx.shortcode_id.sudo()
            if not (shortcode.initiator_name and shortcode.security_credential and tx.receipt):
                continue
            try:
                shortcode._client().transaction_status(
                    initiator=shortcode.initiator_name,
                    security_credential=shortcode.security_credential,
                    transaction_id=tx.receipt,
                    party_a=shortcode.c2b_shortcode or shortcode.number,
                    result_url=shortcode._callback_url("status_result"),
                    timeout_url=shortcode._callback_url("status_timeout"),
                    remarks=_("Check %s", tx.receipt),
                )
            except (daraja.DarajaError, daraja.DarajaTransportError) as exc:
                tx.message_post(body=_("Transaction Status request failed: %s", exc))

    @api.model
    def _from_status_result(self, registration, body):
        result = body.get("Result") or {}
        params = daraja.result_parameters(result)
        receipt = clean_receipt(params.get("ReceiptNo") or result.get("TransactionID"))
        if str(result.get("ResultCode")) != "0" or not receipt:
            _logger.info("Transaction Status for %s: %s", receipt, result.get("ResultDesc"))
            return self.browse()
        if (params.get("TransactionStatus") or "").lower() != "completed":
            return self.browse()
        when = daraja.parse_daraja_time(params.get("FinalisedTime") or params.get("InitiatedTime"))
        vals = {
            "amount": daraja.parse_amount(params.get("Amount")),
            "trans_time": when.replace(tzinfo=None) if when else fields.Datetime.now(),
        }
        tx = self.sudo().search([("company_id", "=", registration.company_id.id),
                                 ("receipt", "=", receipt)], limit=1)
        if tx:
            tx._confirm_known(tx.shortcode_id, vals, "status")
            return tx
        # A status answer is not a C2B event: it confirms what we hold, it does
        # not invent receipts.
        return self.browse()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def _notify_origin(self):
        """Tell whoever is waiting. Overridden by the POS and payment modules."""
        for tx in self:
            if tx.move_id and tx.state in ("confirmed", "failed"):
                if tx.state == "confirmed":
                    body = _("M-PESA payment of %(amount)s received, receipt %(receipt)s.",
                             amount=tx.amount, receipt=tx.receipt or _("pending"))
                else:
                    body = _("M-PESA payment request failed: %s", tx.cashier_message)
                tx.move_id.sudo().message_post(body=body)
            if tx.user_id and tx.state in ("confirmed", "failed") and tx.source == "stk":
                tx.user_id._bus_send("simple_notification", {
                    "type": "success" if tx.state == "confirmed" else "warning",
                    "title": _("M-PESA"),
                    "message": tx.cashier_message or "",
                })

    # ------------------------------------------------------------------
    # Retention (Data Protection Act)
    # ------------------------------------------------------------------

    @api.model
    def _cron_retention(self):
        """Mask full phone numbers and redact raw payloads past the retention period."""
        for company in self.env["res.company"].sudo().search([]):
            days = company.mpesa_phone_retention_days
            if not days:
                continue
            limit = fields.Datetime.now() - timedelta(days=days)
            old = self.sudo().search([("company_id", "=", company.id),
                                      ("phone", "!=", False), ("create_date", "<", limit)])
            for tx in old:
                tx.write({"msisdn_masked": tx.msisdn_masked or daraja.mask_msisdn(tx.phone),
                          "phone": False})
            self.env["mpesa.callback"].sudo().search([
                ("company_id", "=", company.id), ("create_date", "<", limit),
                ("redacted", "=", False)])._redact()

    # ------------------------------------------------------------------
    # Balance check
    # ------------------------------------------------------------------

    @api.model
    def _cron_balance_check(self):
        """Each till's Odoo balance against the latest OrgAccountBalance."""
        for shortcode in self.env["mpesa.shortcode"].sudo().search(
                [("journal_id", "!=", False), ("last_org_balance_at", "!=", False)]):
            last = self.sudo().search([("shortcode_id", "=", shortcode.id),
                                       ("org_balance", "!=", False),
                                       ("statement_line_id", "!=", False)],
                                      order="trans_time desc, id desc", limit=1)
            if not last:
                continue
            line = last.statement_line_id
            lines = self.env["account.bank.statement.line"].sudo().search([
                ("journal_id", "=", shortcode.journal_id.id),
                ("internal_index", "<=", line.internal_index),
                ("state", "=", "posted"),
            ])
            odoo_balance = sum(lines.mapped("amount"))
            difference = shortcode.currency_id.round(last.org_balance - odoo_balance)
            if not shortcode.currency_id.is_zero(difference):
                shortcode.message_post(body=_(
                    "Balance check: M-PESA reported %(mpesa)s after %(receipt)s, Odoo "
                    "has %(odoo)s (difference %(diff)s). Charges and sweeps arrive "
                    "with the statement import; anything else is a missing receipt.",
                    mpesa=last.org_balance, receipt=last.receipt, odoo=odoo_balance,
                    diff=difference))
