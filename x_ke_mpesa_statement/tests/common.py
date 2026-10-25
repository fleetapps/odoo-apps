# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

import base64
from datetime import UTC, datetime, timedelta

from odoo.addons.x_ke_mpesa.lib import daraja
from odoo.addons.x_ke_mpesa.tests.common import MpesaCommon


def pull_row(receipt, amount, when=None, msisdn="254708374149", kind="c2b-buy-goods-debit",
             bill_ref="", shortcode=None):
    """A Pull API row, as Daraja writes it."""
    when = when or datetime.now(UTC)
    row = {
        "transactionId": receipt,
        "trxDate": when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "msisdn": msisdn,
        "sender": "",
        "transactiontype": kind,
        "billreference": bill_ref,
        "amount": f"{amount:.1f}",
        "organizationname": "CHOICE FEEDS",
    }
    if shortcode:
        row["_shortcode"] = shortcode
    return row


def nairobi(moment):
    """The Org Portal's way of writing a time: Nairobi wall time."""
    return moment.astimezone(daraja.NAIROBI).strftime("%Y-%m-%d %H:%M:%S")


class StatementCommon(MpesaCommon):

    def import_statement(self, journal, content, mapping=None):
        """The statement import wizard, as an accountant runs it."""
        mapping = mapping or self.env.ref("x_ke_mpesa_statement.mapping_mpesa_org_portal")
        wizard = self.env["account.statement.import"].with_context(journal_id=journal.id).create({
            "statement_filename": "statement.csv",
            "statement_file": base64.b64encode(content.encode()),
            "sheet_mapping_id": mapping.id,
        })
        return wizard.with_context(journal_id=journal.id)._import_file()

    @staticmethod
    def hours_ago(hours):
        return datetime.now(UTC) - timedelta(hours=hours)
