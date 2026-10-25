# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class AccountStatementImportSheetMapping(models.Model):
    _inherit = "account.statement.import.sheet.mapping"

    mpesa_org_portal = fields.Boolean(
        "M-PESA Org Portal statement",
        help="Rows keep their receipt number as the import id (MPESA-<receipt>), so "
             "a receipt C2B, STK or the Pull API already brought is not booked twice; "
             "charges and sweeps are booked to their accounts. The column header is "
             "found wherever it is, below the account details the portal puts on top.")
    mpesa_status_column = fields.Char(
        "Transaction status column", help="Only rows whose status is Completed are read.")
    mpesa_reason_column = fields.Char(
        "Reason type column", help="Tells receipts, charges and sweeps apart.")
    mpesa_account_column = fields.Char(
        "Account number column", help="The paybill account number the customer typed.")
    mpesa_linked_column = fields.Char(
        "Linked transaction column",
        help="On a reversal, the receipt it reverses.")
