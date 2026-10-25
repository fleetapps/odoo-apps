# -*- coding: utf-8 -*-
"""How a payment method is described to KRA (code class 07)."""

from odoo import api, fields, models

from odoo.addons.x_ke_etims.models.ke_etims_code import CODE_CLASS_PAYMENT_TYPE


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    ke_etims_payment_type_id = fields.Many2one(
        "ke.etims.code", string="eTIMS Payment Type",
        domain=[("code_class", "=", CODE_CLASS_PAYMENT_TYPE)],
        compute="_compute_ke_etims_payment_type_id", store=True, readonly=False,
        help="KRA's payment type for sales paid this way: cash, card, mobile money...")
    ke_etims_show = fields.Boolean(compute="_compute_ke_etims_show")

    @api.depends("company_id")
    def _compute_ke_etims_show(self):
        for method in self:
            method.ke_etims_show = method.company_id.account_fiscal_country_id.code == "KE"

    @api.depends("is_cash_count", "type")
    def _compute_ke_etims_payment_type_id(self):
        Code = self.env["ke.etims.code"]
        for method in self:
            if method.ke_etims_payment_type_id:
                continue
            # Cash is the one KRA type a till can know for certain; anything
            # else is left for the manager to say (card, mobile money...).
            if method.is_cash_count:
                method.ke_etims_payment_type_id = Code._ke_etims_find(CODE_CLASS_PAYMENT_TYPE, "01")
