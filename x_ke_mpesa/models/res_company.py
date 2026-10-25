# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    mpesa_suspense_account_id = fields.Many2one(
        "account.account", string="M-Pesa suspense account", check_company=True,
        help="Shared by every M-Pesa journal: receipts wait here until they are "
             "allocated, and parked receipts stay here.")
    mpesa_rounding_account_id = fields.Many2one(
        "account.account", string="M-Pesa rounding account", check_company=True,
        help="Differences under one shilling between a whole-shilling receipt and "
             "the invoice it closes. Empty: the cash rounding loss account, if any.")
    mpesa_match_stk = fields.Boolean(
        "Match STK receipts to their document", default=True)
    mpesa_match_claims = fields.Boolean(
        "Match POS “paid to till” claims", default=True)
    mpesa_match_paybill = fields.Boolean(
        "Match paybill account numbers", default=True,
        help="An account number equal to an invoice number pays that invoice; equal "
             "to a customer's M-Pesa or internal reference, it pays that customer's "
             "oldest open invoices.")
    mpesa_claim_window = fields.Integer(
        "Paid-to-till window (minutes)", default=30,
        help="How far back the POS lists a till's unclaimed receipts.")
    mpesa_manual_alert_minutes = fields.Integer(
        "Alert on unconfirmed manual receipts after (minutes)", default=30)
    mpesa_supervisor_id = fields.Many2one(
        "res.users", string="M-Pesa supervisor",
        help="Alerted when a till has no supervisor of its own.")
    mpesa_phone_retention_days = fields.Integer(
        "Keep full phone numbers for (days)", default=90,
        help="After this, full numbers from STK Push are masked and raw callbacks "
             "redacted (Data Protection Act, 2019). 0 keeps them.")
    mpesa_transaction_status_check = fields.Boolean(
        "Check manual receipts with Transaction Status",
        help="Ask M-PESA's Transaction Status API about every manually entered "
             "receipt. Needs an initiator with the Transaction Status role.")

    def _mpesa_suspense_account(self):
        """The shared M-Pesa suspense account, created on first use."""
        self.ensure_one()
        if self.mpesa_suspense_account_id:
            return self.mpesa_suspense_account_id
        Account = self.env["account.account"].sudo().with_company(self)
        code = self.env["mpesa.shortcode"]._free_account_code(self, "1106", "00")
        account = Account.create({
            "name": _("M-Pesa suspense"),
            "code": code,
            "account_type": "asset_current",
            "reconcile": False,
        })
        self.sudo().mpesa_suspense_account_id = account
        return account

    def _mpesa_rounding_account(self):
        self.ensure_one()
        if self.mpesa_rounding_account_id:
            return self.mpesa_rounding_account_id
        rounding = self.env["account.cash.rounding"].sudo().search(
            [("company_id", "in", (False, self.id)), ("loss_account_id", "!=", False)], limit=1)
        return rounding.loss_account_id
