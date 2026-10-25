# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    ke_etims_deductible_from = fields.Date(
        string="Expenses Need eTIMS Invoices From",
        default=lambda self: fields.Date.to_date("2026-01-01"),
        help="Bills dated from this day on are flagged when a local supplier "
             "did not back them with an eTIMS invoice. Leave empty to flag none.")
