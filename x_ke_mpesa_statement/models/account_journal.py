# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""An Org Portal statement file, row by row, into the till's daily statements.

The OCA import would make one statement per file. On an M-Pesa journal the
rows go where every other M-Pesa line goes instead: receipts through the
receipt log, charges, sweeps and reversals as lines of their day's
statement. A file imported twice, or a receipt C2B already brought, adds
nothing.
"""

from collections import Counter
from datetime import UTC

from odoo import _, fields, models

from odoo.addons.x_ke_mpesa.lib import daraja

LINE_FIELDS = ("journal_id", "amount", "payment_ref", "ref", "narration", "partner_name",
               "transaction_type", "unique_import_id", "transaction_details")


class AccountJournal(models.Model):
    _inherit = "account.journal"

    def _mpesa_scope_balance(self, date):
        """What this journal holds at the end of ``date``, with its tills' journals
        when it is a store's (M-PESA may keep a store's money in one account)."""
        self.ensure_one()
        shortcodes = self.mpesa_shortcode_ids
        journals = self | (shortcodes | shortcodes.child_ids).journal_id
        accounts = journals.default_account_id
        if not accounts or not date:
            return 0.0
        groups = self.env["account.move.line"].sudo()._read_group(
            [("account_id", "in", accounts.ids), ("date", "<=", date),
             ("parent_state", "=", "posted")], [], ["balance:sum"])
        return (groups[0][0] if groups else 0.0) or 0.0

    def _mpesa_is_statement(self, st_vals):
        rows = st_vals.get("transactions") or []
        return bool(rows) and all(
            (row.get("transaction_details") or {}).get("mpesa_kind") for row in rows)

    def _mpesa_import_statement(self, st_vals, result):
        """Book one parsed Org Portal statement. Returns the statements touched."""
        self.ensure_one()
        shortcode = self.mpesa_shortcode_ids[:1]
        company = self.company_id
        Tx = self.env["mpesa.transaction"].sudo()
        Line = self.env["account.bank.statement.line"].sudo()
        counts = Counter()
        touched = self.env["account.bank.statement"].sudo()
        closing = {}
        seen_ids = Counter()
        for row in st_vals["transactions"]:
            details = row["transaction_details"]
            kind = details["mpesa_kind"]
            moment = fields.Datetime.to_datetime(row["date"])
            day = moment.date()
            if details.get("mpesa_balance") is not None:
                closing[day] = (moment, details["mpesa_balance"])
            if kind == "receipt":
                known = Tx.search_count([("company_id", "=", company.id),
                                         ("receipt", "=", details["mpesa_receipt"])])
                tx = Tx._from_statement_row(shortcode, row)
                counts["known" if known else "new"] += 1
                touched |= tx.statement_line_id.statement_id
                continue
            # The same receipt can carry more than one charge: number the repeats.
            uid = row["unique_import_id"]
            seen_ids[uid] += 1
            if seen_ids[uid] > 1:
                uid = f"{uid}-{seen_ids[uid]}"
            line = Line.search([("unique_import_id", "=", uid)], limit=1)
            if line:
                counts["known"] += 1
                touched |= line.statement_id
                continue
            vals = {key: row[key] for key in LINE_FIELDS if row.get(key) not in (None, "")}
            vals.update(unique_import_id=uid, date=day,
                        statement_id=self._mpesa_daily_statement(day).id)
            line = Line.create(vals)
            line.statement_id._mpesa_sync_balance()
            line._mpesa_book()
            counts[kind] += 1
            touched |= line.statement_id
            if kind == "reversal" and details.get("mpesa_linked"):
                Tx.search([("company_id", "=", company.id),
                           ("receipt", "=", details["mpesa_linked"])])._flag_reversed(
                    details["mpesa_receipt"], line.amount)
        touched |= self._mpesa_record_closing(closing)
        result["notifications"].append(self._mpesa_import_summary(counts))
        return touched

    def _mpesa_record_closing(self, closing):
        """M-PESA's balance after each day's last row, on that day's statement."""
        Statement = self.env["account.bank.statement"].sudo()
        statements = Statement
        for day, (moment, balance) in closing.items():
            statement = Statement.search([("journal_id", "=", self.id),
                                          ("name", "=", self._mpesa_daily_statement_name(day))],
                                         limit=1)
            if not statement:
                continue
            statement.write({
                "mpesa_balance_reported": balance,
                # The portal writes Nairobi wall time; Odoo keeps UTC.
                "mpesa_balance_reported_at": moment.replace(tzinfo=daraja.NAIROBI)
                .astimezone(UTC).replace(tzinfo=None),
            })
            statements |= statement
        return statements

    def _mpesa_import_summary(self, counts):
        parts = [_("%(n)s new receipts", n=counts["new"]),
                 _("%(n)s rows already in Odoo", n=counts["known"])]
        labels = {"charge": _("charges booked"), "sweep": _("sweeps to bank booked"),
                  "reversal": _("reversals to look at"), "other": _("other rows to look at")}
        parts += [f"{counts[kind]} {label}" for kind, label in labels.items() if counts[kind]]
        return _("M-PESA statement: %s.", ", ".join(parts))
