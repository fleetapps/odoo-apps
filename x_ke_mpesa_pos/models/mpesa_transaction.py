# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""POS links on the transaction log, and matching rule 2."""

from odoo import _, api, fields, models


class MpesaTransaction(models.Model):
    _inherit = "mpesa.transaction"

    exception = fields.Selection(
        selection_add=[("pos_difference", "Does not settle against its POS session")],
        ondelete={"pos_difference": "set default"})
    pos_payment_id = fields.Many2one("pos.payment", "POS payment", readonly=True, index=True,
                                     ondelete="set null", copy=False)
    pos_order_id = fields.Many2one(related="pos_payment_id.pos_order_id", store=True,
                                   string="POS order")
    pos_session_id = fields.Many2one("pos.session", "POS session", readonly=True, index=True,
                                     copy=False)
    pos_config_id = fields.Many2one("pos.config", "Point of sale", readonly=True, index=True,
                                    copy=False)
    pos_order_uuid = fields.Char(readonly=True, index=True, copy=False)

    def _match_origin(self):
        if self.pos_session_id:
            return self._match_claim()
        return super()._match_origin()

    def _match_claim(self):
        """Rule 2: a POS receipt settles against its session's M-Pesa payment.

        Until the session closes there is nothing to settle against: the
        receipt shows as claimed. After closing (a manual receipt M-PESA
        confirms late, an STK receipt that arrives after the close) it is
        reconciled at once against what the session left open.
        """
        self.ensure_one()
        session = self.pos_session_id
        if not session or not self.claimed:
            return super()._match_claim()
        if session.state != "closed":
            return True
        return bool(session._mpesa_reconcile_transactions(self))

    def _hand_over_to(self, target):
        """Carry the POS links over when a placeholder merges into ``target``."""
        for fname in ("pos_payment_id", "pos_session_id", "pos_config_id", "pos_order_uuid"):
            if self[fname] and not target[fname]:
                target[fname] = self[fname]
        if self.claimed and not target.claimed:
            target.write({"claimed": True, "claim_ref": self.claim_ref})
        return super()._hand_over_to(target)

    def _release_claim(self, reason):
        for tx in self.filtered("claimed"):
            tx.write({"claimed": False, "claim_ref": False, "pos_order_uuid": False})
            tx.message_post(body=_("Claim released: %s", reason))
            if tx.state == "confirmed":
                tx._auto_match()

    def _move_to_shortcode(self, shortcode):
        """A receipt M-PESA could only place on the store, taken by one of its tills."""
        self.ensure_one()
        line = self.statement_line_id
        if line and not line.is_reconciled and shortcode.journal_id and \
                line.journal_id != shortcode.journal_id:
            statement = line.statement_id
            self.statement_line_id = False
            # Out of its daily statement first: Odoo refuses to delete a line
            # from a complete statement.
            line.statement_id = False
            line.unlink()
            statement._mpesa_sync_balance()
            self.shortcode_id = shortcode
            self._ensure_statement_line()
        else:
            self.shortcode_id = shortcode
        self.message_post(body=_("Placed on %s by the cashier's pick.", shortcode.display_name))

    def _notify_origin(self):
        super()._notify_origin()
        for tx in self.filtered("pos_config_id"):
            tx.pos_config_id._notify(
                "MPESA_UPDATE", self.env["pos.payment.method"]._mpesa_vals(tx))

    @api.model
    def _cron_release_stale_claims(self):
        """Claims an order never confirmed, from sessions long closed."""
        stale = self.sudo().search([
            ("claimed", "=", True), ("pos_payment_id", "=", False),
            ("pos_session_id.state", "=", "closed"),
        ])
        stale._release_claim(_("its POS order was never completed."))
