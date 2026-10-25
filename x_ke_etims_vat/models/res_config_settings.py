# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ke_etims_deductible_from = fields.Date(
        related="company_id.ke_etims_deductible_from", readonly=False)
