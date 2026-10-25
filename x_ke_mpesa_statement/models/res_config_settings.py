# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mpesa_charges_account_id = fields.Many2one(
        related="company_id.mpesa_charges_account_id", readonly=False)
    mpesa_sweep_account_id = fields.Many2one(
        related="company_id.mpesa_sweep_account_id", readonly=False)
    mpesa_charge_words = fields.Char(related="company_id.mpesa_charge_words", readonly=False)
    mpesa_sweep_words = fields.Char(related="company_id.mpesa_sweep_words", readonly=False)
    mpesa_pull_time_zone = fields.Selection(
        related="company_id.mpesa_pull_time_zone", readonly=False)
