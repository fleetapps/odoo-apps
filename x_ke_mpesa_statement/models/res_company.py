# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import _, fields, models

# What the Org Portal statement's reason type or details say about a row
# that takes money out. The export's exact wording is Safaricom's: these are
# settings, checked against a real export before go-live.
DEFAULT_CHARGE_WORDS = "charge, fee"
DEFAULT_SWEEP_WORDS = "settle, to bank, sweep, withdraw"
REVERSAL_WORDS = ("revers",)


def _words(text):
    return [word.strip().lower() for word in (text or "").split(",") if word.strip()]


class ResCompany(models.Model):
    _inherit = "res.company"

    mpesa_charges_account_id = fields.Many2one(
        "account.account", string="M-Pesa charges account", check_company=True,
        help="Expense account for the transaction charges on the M-PESA statement. "
             "Empty: an \"M-Pesa charges\" account is created on first use.")
    mpesa_sweep_account_id = fields.Many2one(
        "account.account", string="M-Pesa sweeps account", check_company=True,
        help="Where money swept from a till to the bank waits until the bank "
             "statement shows it arriving. Empty: the internal transfer account.")
    mpesa_charge_words = fields.Char(
        "Charge rows contain", default=DEFAULT_CHARGE_WORDS,
        help="Comma-separated. A row taking money out whose reason type or details "
             "contain one of these is a charge.")
    mpesa_sweep_words = fields.Char(
        "Sweep rows contain", default=DEFAULT_SWEEP_WORDS,
        help="Comma-separated. A row taking money out to the business's bank account.")
    mpesa_pull_time_zone = fields.Selection(
        [("utc", "UTC, as the trailing Z says"),
         ("nairobi", "Nairobi time, despite the Z")],
        string="Pull API times are in", default="utc", required=True,
        help="The Pull API writes times like 2026-10-25T09:48:33Z. Compare one "
             "receipt's time with the customer's SMS to know which it is.")

    def _mpesa_charges_account(self):
        """The M-Pesa charges expense account, created on first use."""
        self.ensure_one()
        if self.mpesa_charges_account_id:
            return self.mpesa_charges_account_id
        code = self.env["mpesa.shortcode"]._free_account_code(self, "6106", "00")
        account = self.env["account.account"].sudo().with_company(self).create({
            "name": _("M-Pesa charges"),
            "code": code,
            "account_type": "expense",
        })
        self.sudo().mpesa_charges_account_id = account
        return account

    def _mpesa_sweep_account(self):
        self.ensure_one()
        return self.mpesa_sweep_account_id or self.transfer_account_id

    def _mpesa_statement_kind(self, amount, *texts):
        """What an Org Portal statement row is: receipt, charge, sweep,
        reversal or other (left to the accountant)."""
        self.ensure_one()
        if amount > 0:
            return "receipt"
        text = " ".join(str(t) for t in texts if t).lower()
        if any(word in text for word in REVERSAL_WORDS):
            return "reversal"
        if any(word in text for word in _words(self.mpesa_charge_words)):
            return "charge"
        if any(word in text for word in _words(self.mpesa_sweep_words)):
            return "sweep"
        return "other"
