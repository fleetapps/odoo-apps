# -*- coding: utf-8 -*-
"""A Kenyan credit note carries KRA's reason for it from the moment it is made."""

from odoo import api, fields, models

from .ke_etims_code import CODE_CLASS_CREDIT_NOTE_REASON


class AccountMoveReversal(models.TransientModel):
    _inherit = "account.move.reversal"

    ke_etims_reason_code_id = fields.Many2one(
        "ke.etims.code", string="KRA Credit Note Reason",
        domain=[("code_class", "=", CODE_CLASS_CREDIT_NOTE_REASON)])
    ke_etims_required = fields.Boolean(compute="_compute_ke_etims_required")

    @api.depends("move_ids", "company_id")
    def _compute_ke_etims_required(self):
        for wizard in self:
            wizard.ke_etims_required = bool(wizard.move_ids) and all(
                move.country_code == "KE" and move.move_type in ("out_invoice", "out_receipt")
                for move in wizard.move_ids) and wizard.company_id._ke_etims_enabled()

    def _prepare_default_reversal(self, move):
        values = super()._prepare_default_reversal(move)
        if self.ke_etims_reason_code_id:
            values["ke_etims_reason_code_id"] = self.ke_etims_reason_code_id.id
        return values
