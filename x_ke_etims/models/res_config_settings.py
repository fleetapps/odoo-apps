# -*- coding: utf-8 -*-
"""Kenya eTIMS settings: company facts, shown where an accountant looks."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ke_etims_default_backend = fields.Selection(
        related="company_id.ke_etims_default_backend", readonly=False)
    ke_etims_environment = fields.Selection(
        related="company_id.ke_etims_environment", readonly=False)
    ke_etims_auto_send = fields.Boolean(
        related="company_id.ke_etims_auto_send", readonly=False)
    ke_etims_bhf_id = fields.Char(related="company_id.ke_etims_bhf_id", readonly=False)
    ke_etims_default_payment_type_id = fields.Many2one(
        related="company_id.ke_etims_default_payment_type_id", readonly=False)
    ke_etims_active = fields.Boolean(related="company_id.ke_etims_active")

    def action_ke_etims_devices(self):
        return self.company_id.action_ke_etims_devices()
