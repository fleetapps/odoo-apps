# -*- coding: utf-8 -*-
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    ke_etims_no_invoice_needed = fields.Boolean(
        string="No eTIMS Invoice Needed",
        help="This supplier's bills need no eTIMS invoice to be deductible: "
             "a bank's charges and interest, wages, an airline's tickets. Their "
             "bills are not flagged.")
