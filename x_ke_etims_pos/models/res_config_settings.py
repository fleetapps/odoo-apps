# -*- coding: utf-8 -*-
"""The till's eTIMS options, in Point of Sale settings. The pos_ prefix is
how point_of_sale's settings write a field back to the till."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_ke_etims_enabled = fields.Boolean(related="pos_config_id.ke_etims_enabled")
    pos_ke_etims_refund_reason_id = fields.Many2one(
        related="pos_config_id.ke_etims_refund_reason_id", readonly=False)
    pos_ke_etims_sign_timeout = fields.Integer(
        related="pos_config_id.ke_etims_sign_timeout", readonly=False)
