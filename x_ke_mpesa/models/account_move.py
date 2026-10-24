# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    mpesa_transaction_ids = fields.One2many("mpesa.transaction", "move_id", string="M-Pesa requests")
    mpesa_can_request = fields.Boolean(compute="_compute_mpesa_can_request")

    def _compute_mpesa_can_request(self):
        has_stk = bool(self.env["mpesa.shortcode"].sudo().search_count(
            [("stk_enabled", "=", True), ("company_id", "in", self.company_id.ids)], limit=1))
        for move in self:
            move.mpesa_can_request = (
                has_stk and move.move_type == "out_invoice" and move.state == "posted"
                and move.payment_state in ("not_paid", "partial")
                and move.currency_id == move.company_id.currency_id)

    def action_mpesa_request_payment(self):
        """Prompt the customer's phone for this invoice (or part of it)."""
        self.ensure_one()
        if not self.mpesa_can_request:
            raise UserError(_("Only an open customer invoice in shillings can be paid "
                              "with an M-PESA prompt."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Request M-Pesa payment"),
            "res_model": "mpesa.stk.request",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }
