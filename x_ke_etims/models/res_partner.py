# -*- coding: utf-8 -*-
"""A partner's KRA branch. Its KRA PIN is the VAT field it already has."""

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    ke_etims_branch_code = fields.Char(
        string="eTIMS Branch Code", size=2, default="00",
        help="The partner's KRA branch ID, used when confirming their sales to "
             "us as purchases and when moving stock between branches.")
    ke_etims_show = fields.Boolean(compute="_compute_ke_etims_show")

    @api.depends_context("company")
    def _compute_ke_etims_show(self):
        kenyan = self.env.company.account_fiscal_country_id.code == "KE"
        for partner in self:
            partner.ke_etims_show = kenyan
