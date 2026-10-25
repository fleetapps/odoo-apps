# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Reading an M-PESA Org Portal statement with the OCA sheet mapping.

Three things the generic parser does not do: find the column header below
the account details the portal writes on top, read only Completed rows, and
give each row the canonical import id: ``MPESA-<receipt>`` for money in (the
same id C2B, STK and the Pull API give), ``MPESA-<receipt>-CHG`` / ``-SWP`` /
``-REV`` / ``-OUT`` for what goes out.
"""

import re
from datetime import UTC

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.x_ke_mpesa.lib import daraja

from ..models.account_bank_statement import SUFFIXES
from ..models.mpesa_transaction import masked

# "254712345678 - JANE WANJIKU", "2547*****678 - JANE WANJIKU", "JANE WANJIKU"
OTHER_PARTY_RE = re.compile(r"^\s*(\+?[\d*][\d*\s]{6,})\s*-\s*(.*?)\s*$")
# A paybill account number written into the details: "... Acc. INV/2026/0042"
ACCOUNT_IN_DETAILS_RE = re.compile(r"\bAcc(?:ount)?\.?\s*(?:No\.?)?\s*[:#]?\s*(\S+)", re.I)
# A full customer number in the details, masked before it is stored.
PHONE_RE = re.compile(r"\+?\b(?:254|0)[17]\d{8}\b")
MPESA_COLUMNS = ["mpesa_status_column", "mpesa_reason_column", "mpesa_account_column",
                 "mpesa_linked_column"]
# Columns an export may lack (a till's has no account number, say): read when
# present, never a reason to refuse the file.
OPTIONAL_COLUMNS = set(MPESA_COLUMNS) | {"description_column", "partner_name_column",
                                         "balance_column", "notes_column", "reference_column"}


def split_other_party(text):
    """(name, masked number) from the statement's Other Party Info."""
    text = str(text or "").strip()
    match = OTHER_PARTY_RE.match(text)
    if match:
        return match.group(2) or False, masked(match.group(1))
    return text or False, False


class AccountStatementImportSheetParser(models.TransientModel):
    _inherit = "account.statement.import.sheet.parser"

    def _get_column_names(self):
        return super()._get_column_names() + MPESA_COLUMNS

    def _get_column_indexes(self, header, column_name, mapping):
        if mapping.mpesa_org_portal and header and column_name in OPTIONAL_COLUMNS:
            names = [name for name in (mapping[column_name] or "").split(",") if name]
            return [header.index(name) for name in names if name in header]
        return super()._get_column_indexes(header, column_name, mapping)

    @api.model
    def parse_header_csv(self, rows, mapping):
        if not mapping.mpesa_org_portal or mapping.no_header:
            return super().parse_header_csv(rows, mapping)
        wanted = (mapping.transaction_id_column or "").strip()
        for row in rows:
            header = [value.strip().lstrip("﻿") for value in row]
            if wanted in header:
                return header[mapping.offset_column:] if mapping.offset_column else header
        raise UserError(_("No “%s” column in this file. Is it an M-PESA Org Portal "
                          "statement?", wanted))

    def _parse_one_line(self, mapping, currency_code, values, columns):
        if not mapping.mpesa_org_portal:
            return super()._parse_one_line(mapping, currency_code, values, columns)

        def value(column):
            return str(self._get_values_from_column(values, columns, column) or "").strip()

        receipt = daraja.clean_receipt(value("transaction_id_column"))
        status = value("mpesa_status_column")
        # Totals and notes at the foot of the file have no receipt; declined,
        # expired and cancelled rows moved no money.
        if not receipt or (columns["mpesa_status_column"] and status.lower() != "completed"):
            return None
        line = super()._parse_one_line(mapping, currency_code, values, columns)
        if not line or not line.get("amount"):
            return None
        line["mpesa"] = {
            "receipt": receipt,
            "reason": value("mpesa_reason_column"),
            "account": value("mpesa_account_column"),
            "linked": daraja.clean_receipt(value("mpesa_linked_column")),
        }
        return line

    @api.model
    def _convert_line_to_transactions(self, line):
        mpesa = line.get("mpesa")
        if not mpesa:
            return super()._convert_line_to_transactions(line)
        journal = self.env["account.journal"].browse(self.env.context.get("journal_id"))
        company = journal.company_id or self.env.company
        amount = float(line["amount"])
        description = PHONE_RE.sub(lambda m: masked(m.group()), line.get("description") or "")
        kind = company._mpesa_statement_kind(amount, mpesa["reason"], description)
        receipt = mpesa["receipt"]
        uid = f"MPESA-{receipt}" if kind == "receipt" else f"MPESA-{receipt}-{SUFFIXES[kind]}"
        moment = line["timestamp"]
        name, number = split_other_party(line.get("partner_name"))
        account = mpesa["account"]
        if not account and kind == "receipt":
            found = ACCOUNT_IN_DETAILS_RE.search(description)
            account = found.group(1) if found else ""
        balance = line.get("balance")
        return [{
            "date": moment,
            "amount": amount,
            "payment_ref": description or mpesa["reason"] or receipt,
            "ref": receipt,
            "unique_import_id": uid,
            "narration": mpesa["reason"] or False,
            "transaction_type": mpesa["reason"] or False,
            "partner_name": name,
            "transaction_details": {
                "mpesa_source": "statement",
                "mpesa_kind": kind,
                "mpesa_receipt": receipt,
                # The portal writes Nairobi wall time; the receipt log keeps UTC.
                "mpesa_trans_time": fields.Datetime.to_string(
                    moment.replace(tzinfo=daraja.NAIROBI).astimezone(UTC).replace(tzinfo=None)),
                "mpesa_bill_ref": account or False,
                "mpesa_msisdn": number,
                "mpesa_reason": mpesa["reason"] or False,
                "mpesa_linked": mpesa["linked"] or False,
                "mpesa_balance": float(balance) if balance is not None else None,
            },
        }]
