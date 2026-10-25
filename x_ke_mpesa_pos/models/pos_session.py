# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Closing a session settles its M-Pesa receipts as a group.

Odoo books one combined payment per method per session (or one per order
with Identify Customer). The session's M-Pesa statement lines, one per
receipt, are reconciled against what those payments left on the till's
outstanding account. What does not add up is written on the session, and in
the POS session report, rather than forced.
"""

from odoo import _, fields, models


class PosSession(models.Model):
    _inherit = "pos.session"

    mpesa_transaction_ids = fields.One2many("mpesa.transaction", "pos_session_id")
    mpesa_expected = fields.Monetary("M-Pesa taken", readonly=True,
                                     help="What the session's M-Pesa payment lines add up to.")
    mpesa_confirmed = fields.Monetary("M-Pesa confirmed", readonly=True,
                                      help="What M-PESA confirmed for them.")
    mpesa_difference = fields.Monetary("M-Pesa difference", readonly=True)
    mpesa_unconfirmed_count = fields.Integer("Unconfirmed M-Pesa receipts", readonly=True)

    def _validate_session(self, balancing_account=False, amount_to_balance=0,
                          bank_payment_method_diffs=None):
        result = super()._validate_session(balancing_account, amount_to_balance,
                                           bank_payment_method_diffs)
        for session in self.filtered(lambda s: s.state == "closed"):
            session.sudo()._mpesa_close()
        return result

    def _mpesa_methods(self):
        return self.payment_method_ids.filtered(lambda m: m.use_payment_terminal == "mpesa")

    def _mpesa_close(self):
        self.ensure_one()
        txs = self.mpesa_transaction_ids
        dangling = txs.filtered(lambda t: t.claimed and not t.pos_payment_id)
        dangling._release_claim(_("%s closed without an order using it.", self.name))
        self._mpesa_reconcile_transactions(txs - dangling)
        self._mpesa_close_figures()

    def _mpesa_outstanding_lines(self, method):
        payments = self.env["account.payment"].sudo().search([
            ("pos_session_id", "=", self.id), ("pos_payment_method_id", "=", method.id),
            ("state", "!=", "draft"),
        ])
        accounts = payments.outstanding_account_id | method.outstanding_account_id
        return payments.move_id.line_ids.filtered(
            lambda line: line.account_id in accounts and not line.reconciled
            and line.balance > 0
        ).sorted(lambda line: line.id)

    def _mpesa_reconcile_transactions(self, transactions):
        """Reconcile these receipts against the session's payments. Returns
        the receipts that ended up reconciled."""
        self.ensure_one()
        done = self.env["mpesa.transaction"]
        if self.state != "closed":
            return done
        for method in self._mpesa_methods():
            todo = transactions.filtered(
                lambda t: t.pos_payment_id.payment_method_id == method
                and t.state == "confirmed" and t.statement_line_id and not t.is_reconciled
            ).sorted(lambda t: (t.trans_time or t.create_date, t.id))
            for tx in todo:
                counterparts = self._mpesa_outstanding_lines(method)
                if counterparts and tx._reconcile_statement_line(counterparts, rest="suspense"):
                    done |= tx
                elif tx.exception == "none":
                    tx.write({"exception": "pos_difference", "exception_note": _(
                        "%(session)s has no M-Pesa left open to settle %(amount)s "
                        "against: the session took less on M-Pesa than this receipt.",
                        session=self.name, amount=tx.amount)})
        if done and self.mpesa_expected:
            self._mpesa_close_figures()
        return done

    def _mpesa_close_figures(self):
        self.ensure_one()
        methods = self._mpesa_methods()
        if not methods:
            return
        payments = self.order_ids.payment_ids.filtered(lambda p: p.payment_method_id in methods)
        txs = self.env["mpesa.transaction"].sudo().search([("pos_payment_id", "in", payments.ids)])
        expected = sum(payments.mapped("amount"))
        confirmed = sum(txs.filtered(lambda t: t.state == "confirmed").mapped("amount"))
        unconfirmed = txs.filtered(lambda t: t.state == "manual" or t.awaiting_receipt)
        difference = self.currency_id.round(expected - confirmed)
        self.write({
            "mpesa_expected": expected,
            "mpesa_confirmed": confirmed,
            "mpesa_difference": difference,
            "mpesa_unconfirmed_count": len(unconfirmed),
        })
        if not self.currency_id.is_zero(difference) or unconfirmed:
            self.message_post(body=_(
                "M-Pesa at closing: taken %(expected)s, confirmed by M-PESA %(confirmed)s, "
                "difference %(difference)s; %(count)s receipt(s) not yet confirmed.",
                expected=expected, confirmed=confirmed, difference=difference,
                count=len(unconfirmed)))
