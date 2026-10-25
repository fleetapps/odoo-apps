# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Receipts from the Pull API and the Org Portal statement.

Both go through ``_upsert_receipt``, the one way a receipt enters the log: a
receipt C2B or STK already brought changes nothing (or is flagged if the
amount or till differ), one that was missed gets its statement line and is
matched like any other.
"""

import re

from odoo import _, api, fields, models

from odoo.addons.x_ke_mpesa.lib import daraja

# Pull rows that are not a customer paying us.
NOT_A_RECEIPT_RE = re.compile(r"b2c|b2b|revers|charge|withdraw", re.I)


def masked(value):
    """A customer number fit to keep: already masked by Safaricom, or masked here."""
    text = str(value or "").strip()
    if "*" in text:
        return text.replace(" ", "")
    return daraja.mask_msisdn(text) or False


class MpesaTransaction(models.Model):
    _inherit = "mpesa.transaction"

    exception = fields.Selection(
        selection_add=[("reversed", "Reversed by M-PESA")],
        ondelete={"reversed": "set default"})

    @api.model
    def _from_pull(self, shortcode, row):
        """One Pull API row. An empty recordset for a row that is not a receipt."""
        receipt = daraja.clean_receipt(row.get("transactionId"))
        amount = daraja.parse_amount(row.get("amount"))
        kind = str(row.get("transactiontype") or row.get("transactionType") or "")
        if not receipt or not amount or amount <= 0 or NOT_A_RECEIPT_RE.search(kind):
            return self.browse()
        raw_time = row.get("trxDate") or row.get("transactionDate")
        if shortcode.company_id.mpesa_pull_time_zone == "nairobi" and isinstance(raw_time, str):
            raw_time = raw_time.strip().rstrip("Zz")
        moment = daraja.parse_daraja_time(raw_time)
        vals = {
            "amount": amount,
            "trans_time": moment.replace(tzinfo=None) if moment else fields.Datetime.now(),
            "msisdn_masked": masked(row.get("msisdn") or row.get("sender")),
            "bill_ref": str(row.get("billreference") or row.get("billReference") or "").strip()
            or False,
            "transaction_type": kind or False,
        }
        return self._upsert_receipt(shortcode, receipt, vals, "pull")

    @api.model
    def _from_statement_row(self, shortcode, line_vals):
        """A money-in row of an Org Portal statement, as the parser left it."""
        details = line_vals.get("transaction_details") or {}
        vals = {
            "amount": float(line_vals["amount"]),
            "trans_time": fields.Datetime.to_datetime(details.get("mpesa_trans_time"))
            or fields.Datetime.now(),
            "customer_name": line_vals.get("partner_name") or False,
            "msisdn_masked": details.get("mpesa_msisdn") or False,
            "bill_ref": details.get("mpesa_bill_ref") or False,
            "transaction_type": details.get("mpesa_reason") or False,
            "description": line_vals.get("payment_ref") or False,
        }
        if details.get("mpesa_balance") is not None:
            vals["org_balance"] = details["mpesa_balance"]
        return self._upsert_receipt(shortcode, details["mpesa_receipt"], vals, "statement")

    def _flag_reversed(self, row_receipt, amount):
        """M-PESA's statement shows this receipt reversed."""
        for tx in self:
            if tx.exception == "reversed":
                continue
            tx.write({"exception": "reversed", "exception_note": _(
                "Reversed on the M-PESA statement by %(receipt)s (%(amount)s).",
                receipt=row_receipt, amount=amount)})
            tx.message_post(body=tx.exception_note)
