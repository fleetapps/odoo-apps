# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Request M-Pesa payment: an STK prompt for an invoice, or part of it."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..lib import daraja


class MpesaStkRequest(models.TransientModel):
    _name = "mpesa.stk.request"
    _description = "Request M-Pesa Payment"

    move_id = fields.Many2one("account.move", required=True, readonly=True)
    company_id = fields.Many2one(related="move_id.company_id")
    currency_id = fields.Many2one(related="move_id.currency_id")
    partner_id = fields.Many2one(related="move_id.commercial_partner_id")
    amount_due = fields.Monetary(related="move_id.amount_residual", string="Due")
    shortcode_id = fields.Many2one(
        "mpesa.shortcode", string="Pay to", required=True,
        domain="[('stk_enabled', '=', True), ('company_id', 'parent_of', company_id)]",
        default=lambda self: self._default_shortcode())
    phone = fields.Char(required=True, default=lambda self: self._default_phone())
    amount = fields.Integer(
        required=True, default=lambda self: self._default_amount(),
        help="Whole shillings: M-PESA takes no cents. Lower it for a part payment.")
    transaction_id = fields.Many2one("mpesa.transaction", readonly=True)
    state = fields.Selection(related="transaction_id.state")
    message = fields.Char(related="transaction_id.cashier_message")
    receipt = fields.Char(related="transaction_id.receipt")

    def _default_move(self):
        return self.env["account.move"].browse(self.env.context.get("default_move_id"))

    @api.model
    def _default_shortcode(self):
        move = self._default_move()
        Shortcode = self.env["mpesa.shortcode"]
        domain = [("stk_enabled", "=", True),
                  ("company_id", "parent_of", (move.company_id or self.env.company).id)]
        return (Shortcode.search(domain + [("kind", "=", "paybill")], limit=1)
                or Shortcode.search(domain, limit=1))

    @api.model
    def _default_phone(self):
        partner = self._default_move().partner_id
        return partner.phone or False

    @api.model
    def _default_amount(self):
        move = self._default_move()
        return daraja.whole_shillings(move.amount_residual) if move else 0

    def action_send(self):
        self.ensure_one()
        move = self.move_id
        if not move.mpesa_can_request:
            raise UserError(_("This invoice cannot be paid with an M-PESA prompt."))
        if self.amount < 1:
            raise UserError(_("M-PESA cannot take less than KES 1."))
        if self.amount > daraja.whole_shillings(move.amount_residual):
            raise UserError(_("That is more than is due on %s.", move.name))
        key = f"move:{move.id}:{move.amount_residual}:{self.amount}:{self.phone}"
        tx = self.env["mpesa.transaction"].sudo()._stk_request(
            self.shortcode_id, self.amount, self.phone,
            reference=move.name, description=_("Invoice %s", move.name),
            origin=move, idempotency_key=key, partner=move.commercial_partner_id)
        self.transaction_id = tx
        if tx.state == "failed":
            move.message_post(body=_("M-PESA prompt to %(phone)s failed: %(why)s",
                                     phone=tx.msisdn_masked, why=tx.cashier_message))
        else:
            move.message_post(body=_("M-PESA prompt for %(amount)s sent to %(phone)s.",
                                     amount=tx.amount, phone=tx.msisdn_masked))
        return self._reopen()

    def action_refresh(self):
        return self._reopen()

    def action_query(self):
        self.transaction_id.sudo()._stk_query()
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Request M-Pesa payment"),
        }
