# -*- coding: utf-8 -*-
"""Kenyan VAT settings, surfaced where an accountant will look for them.

Everything here is a view onto ``res.company``. The fields live on the company
because they are company facts; they appear in Settings because that is where
someone configuring a chart of accounts expects to find them.
"""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ke_vat_post_on_close = fields.Boolean(
        related="company_id.ke_vat_post_on_close", readonly=False)
    ke_vat_journal_id = fields.Many2one(
        related="company_id.ke_vat_journal_id", readonly=False)
    ke_vat_output_account_id = fields.Many2one(
        related="company_id.ke_vat_output_account_id", readonly=False)
    ke_vat_input_account_id = fields.Many2one(
        related="company_id.ke_vat_input_account_id", readonly=False)
    ke_vat_payable_account_id = fields.Many2one(
        related="company_id.ke_vat_payable_account_id", readonly=False)
    ke_vat_credit_account_id = fields.Many2one(
        related="company_id.ke_vat_credit_account_id", readonly=False)
    ke_vat_non_deductible_account_id = fields.Many2one(
        related="company_id.ke_vat_non_deductible_account_id", readonly=False)
    ke_vat_import_vat_account_id = fields.Many2one(
        related="company_id.ke_vat_import_vat_account_id", readonly=False)
    ke_vat_tax_lock_date = fields.Date(
        related="company_id.tax_lock_date", readonly=False,
        string="Tax Return Lock Date",
        help="Set automatically when a VAT return is closed. Reopening a "
             "return deliberately leaves it alone, so this is where you lift a "
             "lock you have decided to lift.")
