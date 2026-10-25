# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The POS payment line knows its receipt; the transaction knows its line.

Linking happens when the order reaches the server, which may be long after
the payment for an order taken offline. It never refuses the order: a
payment that does not add up is flagged on the transaction instead, because
an order the server refuses is an order nobody can close the session with.
"""

import logging

from odoo import _, api, fields, models
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class PosPayment(models.Model):
    _inherit = "pos.payment"

    mpesa_receipt = fields.Char("M-Pesa receipt", readonly=True)
    mpesa_manual = fields.Boolean("M-Pesa receipt typed by hand", readonly=True)
    # An id rather than a Many2one: pos.payment fields all travel to the POS,
    # and the POS does not load mpesa.transaction.
    mpesa_tx_ref = fields.Integer("M-Pesa transaction", readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        for payment in payments.filtered(
                lambda p: p.payment_method_id.use_payment_terminal == "mpesa"):
            try:
                with self.env.cr.savepoint():
                    payment._mpesa_link()
            except Exception:  # noqa: BLE001 - never lose an order over this
                _logger.exception("Could not link M-Pesa payment %s", payment.id)
        return payments

    def _mpesa_link(self):
        self.ensure_one()
        Tx = self.env["mpesa.transaction"].sudo()
        tx = Tx.browse(self.mpesa_tx_ref).exists() if self.mpesa_tx_ref else Tx
        session = self.session_id
        method = self.payment_method_id
        if not tx and self.mpesa_receipt:
            # Typed while offline: the server hears of it only now.
            shortcode = method.mpesa_shortcode_id.sudo()
            tx = Tx.search([("company_id", "=", shortcode.company_id.id),
                            ("receipt", "=", self.mpesa_receipt.upper())], limit=1)
            if not tx:
                tx = Tx._manual_entry(shortcode, self.mpesa_receipt, self.amount, extra_vals={
                    "pos_session_id": session.id, "pos_config_id": session.config_id.id,
                    "claimed": True, "claim_ref": self.pos_order_id.name,
                })
        if not tx:
            return
        if tx.pos_payment_id and tx.pos_payment_id != self:
            tx.write({"exception": "double_claim", "exception_note": _(
                "Receipt %(receipt)s pays %(first)s and also %(second)s.",
                receipt=tx.receipt, first=tx.pos_payment_id.pos_order_id.name,
                second=self.pos_order_id.name)})
            return
        vals = {
            "pos_payment_id": self.id,
            "pos_session_id": tx.pos_session_id.id or session.id,
            "pos_config_id": tx.pos_config_id.id or session.config_id.id,
            "claimed": True,
            "claim_ref": self.pos_order_id.name,
        }
        if not tx.partner_id and self.pos_order_id.partner_id:
            vals["partner_id"] = self.pos_order_id.partner_id.commercial_partner_id.id
        if float_compare(tx.amount, self.amount, precision_digits=2) != 0 and tx.exception == "none":
            vals.update(exception="amount_mismatch", exception_note=_(
                "%(order)s took %(taken)s on this receipt of %(amount)s.",
                order=self.pos_order_id.name, taken=self.amount, amount=tx.amount))
        tx.write(vals)
        self.write({"mpesa_receipt": tx.receipt or self.mpesa_receipt,
                    "transaction_id": tx.receipt or self.transaction_id,
                    "mpesa_manual": tx.state == "manual"})
