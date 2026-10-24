# -*- coding: utf-8 -*-
"""A till signs through its company's eTIMS device, if the company has one."""

from odoo import api, fields, models

from odoo.addons.x_ke_etims.models.ke_etims_code import CODE_CLASS_CREDIT_NOTE_REASON


class PosConfig(models.Model):
    _inherit = "pos.config"

    ke_etims_enabled = fields.Boolean(
        compute="_compute_ke_etims_enabled", string="eTIMS",
        help="A Kenyan company with an eTIMS device: this till's orders are signed.")
    ke_etims_refund_reason_id = fields.Many2one(
        "ke.etims.code", string="eTIMS Refund Reason",
        domain=[("code_class", "=", CODE_CLASS_CREDIT_NOTE_REASON)],
        default=lambda self: self.env["ke.etims.code"]._ke_etims_find(
            CODE_CLASS_CREDIT_NOTE_REASON, "06"),
        help="The KRA credit note reason sent with this till's refunds.")
    ke_etims_sign_timeout = fields.Integer(
        string="eTIMS Wait (seconds)", default=5,
        help="How long the till waits for the signature before printing the "
             "receipt as pending. The order is signed later either way.")

    @api.depends("company_id")
    def _compute_ke_etims_enabled(self):
        for config in self:
            config.ke_etims_enabled = config.company_id._ke_etims_enabled()
