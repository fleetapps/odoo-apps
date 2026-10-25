# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The M-Pesa payment terminal: one payment method per till.

Everything the POS asks goes through the ``mpesa_*`` methods below, each of
which checks first that the calling session belongs to a point of sale this
method, and this till, may be used at. That check is what stops a cashier at
Outlet X from collecting into Outlet Y's till, or claiming its receipts.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, UserError, ValidationError

from odoo.addons.x_ke_mpesa.lib import daraja


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    mpesa_shortcode_id = fields.Many2one(
        "mpesa.shortcode", string="M-Pesa till", check_company=True,
        help="The till this method collects into. Its journal must be this "
             "method's journal.")
    mpesa_till_number = fields.Char(related="mpesa_shortcode_id.number")
    mpesa_allow_stk = fields.Boolean("Prompt the customer's phone", default=True)
    mpesa_allow_claim = fields.Boolean("Pick a payment made to the till", default=True)
    mpesa_allow_manual = fields.Boolean(
        "Type a receipt number", default=True,
        help="For when Daraja is down. The receipt stays flagged until M-PESA "
             "confirms it, and the supervisor is alerted if it never does.")
    mpesa_claim_any_amount = fields.Boolean(
        "Offer receipts of any amount",
        help="Off: the till's receipts are only offered when their amount is "
             "exactly what the line asks for.")
    mpesa_rounding_warning = fields.Char(compute="_compute_mpesa_rounding_warning")

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [("mpesa", "M-Pesa")]

    @api.model
    def _load_pos_data_fields(self, config):
        return super()._load_pos_data_fields(config) + [
            "mpesa_till_number", "mpesa_allow_stk", "mpesa_allow_claim",
            "mpesa_allow_manual", "mpesa_claim_any_amount",
        ]

    @api.depends("use_payment_terminal", "config_ids.cash_rounding",
                 "config_ids.only_round_cash_method", "config_ids.rounding_method")
    def _compute_mpesa_rounding_warning(self):
        for method in self:
            if method.use_payment_terminal != "mpesa":
                method.mpesa_rounding_warning = False
                continue
            loose = method.config_ids.filtered(
                lambda c: not c.cash_rounding or c.only_round_cash_method
                or (c.rounding_method.rounding or 0) < 1)
            method.mpesa_rounding_warning = loose and _(
                "%s does not round every payment to whole shillings. M-PESA takes "
                "no cents: turn on Cash Rounding (1.00) for all payment methods, or "
                "cashiers will have to take the cents in cash.",
                ", ".join(loose.mapped("name"))) or False

    @api.onchange("mpesa_shortcode_id")
    def _onchange_mpesa_shortcode_id(self):
        journal = self.mpesa_shortcode_id.journal_id
        if journal:
            self.journal_id = journal
            outstanding = journal.inbound_payment_method_line_ids.payment_account_id[:1]
            if outstanding:
                self.outstanding_account_id = outstanding

    @api.constrains("use_payment_terminal", "mpesa_shortcode_id", "journal_id")
    def _check_mpesa_till(self):
        for method in self.filtered(lambda m: m.use_payment_terminal == "mpesa"):
            shortcode = method.mpesa_shortcode_id
            if not shortcode:
                raise ValidationError(_("Choose the M-Pesa till of %s.", method.name))
            if method.journal_id != shortcode.journal_id:
                raise ValidationError(_(
                    "%(method)s collects into %(till)s, so its journal must be "
                    "%(journal)s.", method=method.name, till=shortcode.display_name,
                    journal=shortcode.journal_id.display_name or _("the till's journal")))

    @api.constrains("config_ids", "mpesa_shortcode_id")
    def _check_mpesa_configs(self):
        for method in self.filtered("mpesa_shortcode_id"):
            allowed = method.mpesa_shortcode_id.pos_config_ids
            stray = method.config_ids - allowed if allowed else method.config_ids.browse()
            if stray:
                raise ValidationError(_(
                    "%(till)s may only be used at %(allowed)s, not at %(stray)s.",
                    till=method.mpesa_shortcode_id.display_name,
                    allowed=", ".join(allowed.mapped("name")),
                    stray=", ".join(stray.mapped("name"))))

    # ------------------------------------------------------------------
    # Calls from the POS
    # ------------------------------------------------------------------

    def _mpesa_context(self, data):
        """The session and till a POS call may act on, or an error."""
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessDenied()
        if self.use_payment_terminal != "mpesa" or not self.mpesa_shortcode_id:
            raise UserError(_("%s is not an M-Pesa payment method.", self.name))
        session = self.env["pos.session"].browse(int(data.get("session_id") or 0)).exists()
        if not session or session.state == "closed":
            raise UserError(_("This point of sale session is closed."))
        if self not in session.config_id.payment_method_ids:
            raise UserError(_("%(method)s is not a payment method of %(pos)s.",
                              method=self.name, pos=session.config_id.name))
        shortcode = self.mpesa_shortcode_id.sudo()
        if shortcode.pos_config_ids and session.config_id not in shortcode.pos_config_ids:
            raise UserError(_("%(till)s cannot be used at %(pos)s.",
                              till=shortcode.display_name, pos=session.config_id.name))
        return session, shortcode

    @api.model
    def _mpesa_vals(self, tx):
        """What the POS needs to know about a transaction."""
        return {
            "id": tx.id,
            "state": tx.state,
            "message": tx.cashier_message or "",
            "receipt": tx.receipt or "",
            "amount": tx.amount,
            "manual": tx.state == "manual",
            "confirming": bool(tx.pending_callback),
            "awaiting_receipt": tx.awaiting_receipt,
        }

    @api.model
    def _mpesa_error(self, message):
        return {"error": str(message)}

    def _mpesa_claim_vals(self, session, data):
        return {
            "claimed": True,
            "claim_ref": data.get("order_name") or data.get("order_uuid") or session.name,
            "pos_session_id": session.id,
            "pos_config_id": session.config_id.id,
            "pos_order_uuid": data.get("order_uuid") or False,
        }

    def mpesa_stk_request(self, data):
        """Prompt the customer's phone for a payment line."""
        session, shortcode = self._mpesa_context(data)
        if not self.mpesa_allow_stk:
            return self._mpesa_error(_("Prompting phones is off for %s.", self.name))
        amount = float(data.get("amount") or 0)
        if amount != int(amount) or amount < 1:
            return self._mpesa_error(_("M-PESA takes whole shillings, at least KES 1."))
        partner = self.env["res.partner"].browse(int(data.get("partner_id") or 0)).exists()
        try:
            tx = self.env["mpesa.transaction"].sudo()._stk_request(
                shortcode, amount, data.get("phone"),
                reference=data.get("order_name") or session.config_id.name,
                description=_("POS %s", session.config_id.name),
                idempotency_key=f"pos:{data.get('line_uuid')}" if data.get("line_uuid") else None,
                partner=partner or None,
                extra_vals=self._mpesa_claim_vals(session, data),
            )
        except UserError as exc:
            return self._mpesa_error(exc.args[0])
        return self._mpesa_vals(tx)

    def _mpesa_own_transaction(self, session, transaction_id):
        tx = self.env["mpesa.transaction"].sudo().browse(int(transaction_id or 0)).exists()
        if not tx or tx.pos_config_id != session.config_id:
            raise UserError(_("This M-Pesa payment does not belong to this point of sale."))
        return tx

    def mpesa_status(self, data):
        """Polling fallback for the bus: where is this STK request now?"""
        session, _shortcode = self._mpesa_context(data)
        tx = self._mpesa_own_transaction(session, data.get("transaction_id"))
        now = fields.Datetime.now()
        if (tx.state == "requested" and tx.requested_at
                and tx.requested_at + timedelta(seconds=tx.shortcode_id.stk_query_delay or 90) <= now
                and (not tx.last_query_at or tx.last_query_at + timedelta(seconds=15) <= now)):
            # The cron may lag behind a waiting customer: ask M-PESA now.
            tx._stk_query()
        return self._mpesa_vals(tx)

    def mpesa_unclaimed_receipts(self, data):
        """This till's recent receipts nobody has claimed: "paid to till"."""
        session, shortcode = self._mpesa_context(data)
        if not self.mpesa_allow_claim:
            return []
        window = shortcode.company_id.mpesa_claim_window or 30
        # The store's unidentified receipts too: when a confirmation could not
        # say which till was paid, the cashier's pick decides.
        shortcodes = shortcode | shortcode.parent_id
        domain = [
            ("shortcode_id", "in", shortcodes.ids),
            ("state", "=", "confirmed"),
            ("claimed", "=", False),
            ("is_reconciled", "=", False),
            ("park_reason", "=", False),
            ("source", "in", ("c2b", "pull", "status")),
            ("receipt", "!=", False),
            ("trans_time", ">=", fields.Datetime.now() - timedelta(minutes=window)),
        ]
        if not self.mpesa_claim_any_amount:
            domain.append(("amount", "=", float(data.get("amount") or 0)))
        receipts = self.env["mpesa.transaction"].sudo().search(
            domain, order="trans_time desc", limit=30)
        return [{
            "id": tx.id,
            "amount": tx.amount,
            "time": fields.Datetime.to_string(tx.trans_time),
            "name": tx.customer_name or "",
            "msisdn": tx.msisdn_masked or "",
            "receipt_tail": (tx.receipt or "")[-4:],
        } for tx in receipts]

    def mpesa_claim(self, data):
        """The cashier picked a payment made to the till."""
        session, shortcode = self._mpesa_context(data)
        if not self.mpesa_allow_claim:
            return self._mpesa_error(_("Picking till payments is off for %s.", self.name))
        Tx = self.env["mpesa.transaction"].sudo()
        tx = Tx.browse(int(data.get("transaction_id") or 0)).exists()
        if not tx or not tx.receipt:
            return self._mpesa_error(_("That payment is no longer available."))
        Tx._lock("receipt", tx.company_id.id, tx.receipt)
        tx.invalidate_recordset()
        return self._mpesa_take(tx, session, shortcode, data)

    def _mpesa_take(self, tx, session, shortcode, data):
        if tx.claimed or tx.is_reconciled or tx.state != "confirmed":
            return self._mpesa_error(_("Receipt %s has already been used.", tx.receipt))
        if tx.shortcode_id not in (shortcode | shortcode.parent_id):
            return self._mpesa_error(_("Receipt %(receipt)s was paid to %(till)s.",
                                       receipt=tx.receipt, till=tx.shortcode_id.display_name))
        amount = float(data.get("amount") or 0)
        if not self.mpesa_claim_any_amount and amount and tx.amount != amount:
            return self._mpesa_error(_("Receipt %(receipt)s is for %(amount)s.",
                                       receipt=tx.receipt, amount=tx.amount))
        if tx.shortcode_id != shortcode:
            tx._move_to_shortcode(shortcode)
        tx.write(self._mpesa_claim_vals(session, data))
        tx.message_post(body=_("Claimed by %(user)s at %(pos)s for %(order)s.",
                               user=self.env.user.name, pos=session.config_id.name,
                               order=tx.claim_ref))
        return self._mpesa_vals(tx)

    def mpesa_manual_receipt(self, data):
        """A receipt typed by the cashier (Daraja down, or the customer shows an SMS)."""
        session, shortcode = self._mpesa_context(data)
        if not self.mpesa_allow_manual:
            return self._mpesa_error(_("Typing receipts is off for %s.", self.name))
        Tx = self.env["mpesa.transaction"].sudo()
        try:
            tx = Tx._manual_entry(shortcode, data.get("receipt"), data.get("amount"),
                                  extra_vals=self._mpesa_claim_vals(session, data))
        except UserError as exc:
            return self._mpesa_error(exc.args[0])
        if tx.state == "confirmed" and not tx.claimed:
            # M-PESA already confirmed it: an ordinary pick.
            return self._mpesa_take(tx, session, shortcode, data)
        return self._mpesa_vals(tx)

    def mpesa_release(self, data):
        """The payment line was removed: free whatever it held."""
        session, _shortcode = self._mpesa_context(data)
        tx = self._mpesa_own_transaction(session, data.get("transaction_id"))
        if tx.pos_payment_id or tx.pos_order_uuid != data.get("order_uuid"):
            return False
        tx._release_claim(_("Removed from %s at the till.", data.get("order_name") or _("an order")))
        return True
