# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_cost_plan_id = fields.Many2one(
        related="company_id.odin_cost_plan_id",
        string="Cost Control Analytic Plan",
        readonly=False,
    )
