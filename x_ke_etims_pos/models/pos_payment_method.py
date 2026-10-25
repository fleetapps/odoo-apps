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

    @api.depends("is_cash_count", "type", "name", "use_payment_terminal")
    def _compute_ke_etims_payment_type_id(self):
        Code = self.env["ke.etims.code"]
        for method in self:
            if method.ke_etims_payment_type_id:
                continue
            code = method._ke_etims_guess_payment_type()
            if code:
                method.ke_etims_payment_type_id = Code._ke_etims_find(CODE_CLASS_PAYMENT_TYPE, code)

    def _ke_etims_guess_payment_type(self):
        """KRA's type from what the method is, or what it is called: cash,
        customer account (credit), M-Pesa and the like, card, cheque. A guess
        the manager can change; nothing when there is no telling."""
        self.ensure_one()
        name = (self.name or "").lower().replace("-", "").replace(" ", "")
        if self.is_cash_count:
            return "01"
        if self.type == "pay_later":
            return "02"
        if any(word in name for word in ("mpesa", "airtel", "tkash", "mobile", "paybill",
                                          "till", "lipana")):
            return "06"
        if self.use_payment_terminal or any(word in name for word in (
                "card", "visa", "mastercard", "pdq", "amex")):
            return "05"
        if "cheque" in name or "check" in name:
            return "04"
        return None
