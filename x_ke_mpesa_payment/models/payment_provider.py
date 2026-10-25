# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""M-Pesa as a payment provider: STK Push to the customer's phone.

The provider collects into one till or paybill. Its payment journal is that
shortcode's journal and its payments wait on the shortcode's outstanding
receipts account, so the receipt's statement line meets the payment there,
as a POS session's does.
"""

from odoo import api, fields, models


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    code = fields.Selection(selection_add=[("mpesa", "M-Pesa")], ondelete={"mpesa": "set default"})
    mpesa_shortcode_id = fields.Many2one(
        "mpesa.shortcode", string="M-Pesa till or paybill", required_if_provider="mpesa",
        check_company=True, domain="[('journal_id', '!=', False)]",
        help="Where customers pay. Its journal becomes this provider's payment journal.")

    def _get_supported_currencies(self):
        supported = super()._get_supported_currencies()
        if self.code == "mpesa":
            supported = supported.filtered(lambda currency: currency.name == "KES")
        return supported

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != "mpesa":
            return super()._get_default_payment_method_codes()
        # Odoo's own "M-Pesa" payment method (Kenya, shillings).
        return {"mpesa"}

    @api.model_create_multi
    def create(self, vals_list):
        providers = super().create(vals_list)
        providers._mpesa_sync_journal()
        return providers

    def write(self, vals):
        res = super().write(vals)
        if {"mpesa_shortcode_id", "state", "company_id", "journal_id"} & set(vals):
            self._mpesa_sync_journal()
        return res

    def _mpesa_sync_journal(self):
        """The shortcode's journal, with this provider's payments on its
        outstanding receipts account."""
        for provider in self.filtered(lambda p: p.code == "mpesa"):
            journal = provider.mpesa_shortcode_id.journal_id
            if not journal:
                continue
            if provider.journal_id != journal:
                provider.journal_id = journal
            outstanding = journal.inbound_payment_method_line_ids.filtered(
                lambda line: not line.payment_provider_id).payment_account_id[:1]
            line = journal.inbound_payment_method_line_ids.filtered(
                lambda line, p=provider: line.payment_provider_id == p)
            if outstanding and line and line.payment_account_id != outstanding:
                line.payment_account_id = outstanding
