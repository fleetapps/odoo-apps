# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Charges and sweeps on the till's statement, and M-PESA's own balance."""

from odoo import _, api, fields, models

from odoo.addons.x_ke_mpesa.models.mpesa_reconcile import RECEIPT_UID_RE

LINE_KINDS = [
    ("receipt", "Receipt"),
    ("charge", "Charge"),
    ("sweep", "Sweep to bank"),
    ("reversal", "Reversal"),
    ("other", "Other"),
]
# MPESA-<receipt>-<suffix>: what is not a customer receipt.
SUFFIXES = {"charge": "CHG", "sweep": "SWP", "reversal": "REV", "other": "OUT"}
KIND_BY_SUFFIX = {suffix: kind for kind, suffix in SUFFIXES.items()}


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    mpesa_line_kind = fields.Selection(
        LINE_KINDS, string="M-Pesa line", compute="_compute_mpesa_line_kind", store=True,
        index=True)

    @api.depends("unique_import_id")
    def _compute_mpesa_line_kind(self):
        for line in self:
            uid = line.unique_import_id or ""
            if not uid.startswith("MPESA-"):
                line.mpesa_line_kind = False
            elif RECEIPT_UID_RE.fullmatch(uid):
                line.mpesa_line_kind = "receipt"
            else:
                suffix = uid.split("-")[2] if uid.count("-") >= 2 else ""
                line.mpesa_line_kind = KIND_BY_SUFFIX.get(suffix, "other")

    def _mpesa_book(self):
        """Book charges and sweeps where they belong; the rest waits for the
        accountant in the reconciliation widget."""
        for line in self.filtered(lambda l: not l.is_reconciled):
            company = line.company_id
            if line.mpesa_line_kind == "charge":
                account, label = company._mpesa_charges_account(), _("M-PESA charge")
            elif line.mpesa_line_kind == "sweep":
                account, label = company._mpesa_sweep_account(), _("M-PESA sweep to bank")
            else:
                continue
            if account:
                line._mpesa_book_to(account, f"{label} {line.ref or ''}".strip())

    def _mpesa_book_to(self, account, label):
        """The whole line against ``account``, as an accountant would in the widget."""
        self.ensure_one()
        data_info = self._default_reconcile_data()
        suspense = [item for item in data_info["data"] if item["kind"] == "suspense"]
        if not suspense:
            return self.is_reconciled
        others = [item for item in data_info["data"] if item["kind"] != "suspense"]
        line = dict(suspense[0], kind="other", account_id=[account.id, account.display_name],
                    name=label,
                    reference=f"reconcile_auxiliary;{data_info['reconcile_auxiliary_id']}")
        data_info = self._recompute_suspense_line(
            others + [line], data_info["reconcile_auxiliary_id"] + 1,
            data_info.get("manual_reference"))
        if not data_info.get("can_reconcile"):
            return False
        self.reconcile_data_info = data_info
        self.reconcile_bank_line()
        return True


class AccountBankStatement(models.Model):
    _inherit = "account.bank.statement"

    mpesa_balance_reported = fields.Monetary(
        "M-PESA balance", readonly=True, copy=False,
        help="The balance M-PESA's own statement shows after the day's last row.")
    mpesa_balance_reported_at = fields.Datetime("M-PESA balance at", readonly=True, copy=False)
    mpesa_balance_odoo = fields.Monetary(
        "Odoo balance", compute="_compute_mpesa_balance_check",
        help="What the till's journal (with its tills, for a store) holds at the end "
             "of the same day.")
    mpesa_balance_gap = fields.Monetary(
        "Balance difference", compute="_compute_mpesa_balance_check",
        help="M-PESA's balance minus Odoo's: an opening balance not yet booked, or "
             "rows Odoo does not have.")

    @api.depends("mpesa_balance_reported", "mpesa_balance_reported_at", "line_ids.amount")
    def _compute_mpesa_balance_check(self):
        for statement in self:
            if not statement.mpesa_balance_reported_at:
                statement.mpesa_balance_odoo = statement.mpesa_balance_gap = 0.0
                continue
            odoo = statement.journal_id._mpesa_scope_balance(statement.date)
            statement.mpesa_balance_odoo = odoo
            statement.mpesa_balance_gap = statement.currency_id.round(
                statement.mpesa_balance_reported - odoo)
