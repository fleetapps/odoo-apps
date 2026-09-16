# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    odin_lc_id = fields.Many2one(
        "odin.letter.of.credit",
        string="Letter of Credit",
        index="btree_not_null",
        tracking=True,
        copy=False,
        help="Settle this document under a documentary credit. Payment is then "
        "released only once the credit's document set is complete.",
    )
    odin_lc_documents_complete = fields.Boolean(
        related="odin_lc_id.documents_complete",
        string="LC Documents Complete",
    )
    odin_lc_documents_outstanding = fields.Integer(
        compute="_compute_odin_lc_documents_outstanding",
        string="Documents Outstanding",
    )
    odin_lc_payment_blocked = fields.Boolean(
        compute="_compute_odin_lc_payment_blocked",
        help="True while the documentary conditions are unmet and payment is held.",
    )

    @api.depends(
        "odin_lc_id.documents_required_count", "odin_lc_id.documents_received_count"
    )
    def _compute_odin_lc_documents_outstanding(self):
        for move in self:
            lc = move.odin_lc_id
            move.odin_lc_documents_outstanding = (
                lc.documents_required_count - lc.documents_received_count if lc else 0
            )

    @api.depends("odin_lc_id", "odin_lc_id.documents_complete", "payment_state")
    def _compute_odin_lc_payment_blocked(self):
        for move in self:
            move.odin_lc_payment_blocked = bool(
                move.odin_lc_id
                and not move.odin_lc_id.documents_complete
                and move.payment_state not in ("paid", "reversed")
            )

    def action_view_odin_lc(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "odin.letter.of.credit",
            "res_id": self.odin_lc_id.id,
            "view_mode": "form",
        }
