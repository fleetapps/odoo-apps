# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class MpesaTransaction(models.Model):
    _inherit = "mpesa.transaction"

    payment_transaction_id = fields.Many2one(
        "payment.transaction", string="Online payment",
        compute="_compute_payment_transaction_id")

    def _compute_payment_transaction_id(self):
        for tx in self:
            tx.payment_transaction_id = tx._payment_transaction()

    def _payment_transaction(self):
        self.ensure_one()
        if self.res_model == "payment.transaction" and self.res_id:
            return self.env["payment.transaction"].sudo().browse(self.res_id).exists()
        return self.env["payment.transaction"]

    def _notify_origin(self):
        super()._notify_origin()
        for tx in self:
            if tx.state in ("confirmed", "failed"):
                tx._payment_transaction()._mpesa_update()

    def _match_origin(self):
        """Rule 1 for an online payment: the receipt meets the payment Odoo
        created for it, on the outstanding receipts account."""
        self.ensure_one()
        online = self._payment_transaction()
        if not online:
            return super()._match_origin()
        if online.state in ("cancel", "error"):
            # Paid, but the payment did not go through (a different amount):
            # the other rules, or the queue.
            return False
        payment = online.payment_id
        if not payment:
            # Settles when Odoo creates the payment; until then no other rule
            # may take it, or the invoice would be paid twice.
            return True
        lines = payment.move_id.line_ids.filtered(
            lambda line: line.account_id == payment.outstanding_account_id
            and not line.reconciled)
        if not lines:
            return False
        return self._reconcile_statement_line(lines, partner=payment.partner_id,
                                              rest="partner", round_off=True)
