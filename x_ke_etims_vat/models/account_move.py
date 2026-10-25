# -*- coding: utf-8 -*-
"""Supplier bills a local supplier did not back with an eTIMS invoice.

From the 2026 year of income an expense needs the supplier's eTIMS invoice to
be deductible (Income Tax Act s.16(1)(c)). The bill keeps the supplier's CU
invoice number in ``ke_etims_supplier_invoice_ref``; a bill without one is
flagged while there is still time to ask the supplier for it.

Not every expense is in scope: imports are backed by a customs entry, and a
bank's charges, wages or interest by other documents. Those are marked on the
supplier, or on the one bill, and are left alone.
"""

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.fields import Domain

EXPENSE_TYPES = ("in_invoice", "in_receipt")


class AccountMove(models.Model):
    _inherit = "account.move"

    ke_etims_not_required = fields.Boolean(
        string="No eTIMS Invoice Needed", copy=False, tracking=True,
        help="This expense does not need the supplier's eTIMS invoice to be "
             "deductible, e.g. an import backed by a customs entry.")
    ke_etims_invoice_missing = fields.Boolean(
        string="eTIMS Invoice Missing",
        compute="_compute_ke_etims_invoice_missing",
        search="_search_ke_etims_invoice_missing",
        help="A local supplier's bill with no eTIMS invoice number, dated on or "
             "after the date the company's expenses need one.")

    @api.depends("move_type", "invoice_date", "ke_etims_supplier_invoice_ref",
                 "ke_etims_not_required", "ke_etims_reverse_invoice",
                 "commercial_partner_id.ke_etims_no_invoice_needed",
                 "commercial_partner_id.country_id", "company_id.ke_etims_deductible_from")
    def _compute_ke_etims_invoice_missing(self):
        for move in self:
            partner = move.commercial_partner_id
            start = move.company_id.ke_etims_deductible_from
            move.ke_etims_invoice_missing = bool(
                move.country_code == "KE"
                and move.move_type in EXPENSE_TYPES
                and not move.ke_etims_supplier_invoice_ref
                and not move.ke_etims_not_required
                # Ours to issue: the eTIMS status says whether it is signed.
                and not move.ke_etims_reverse_invoice
                and not partner.ke_etims_no_invoice_needed
                and partner.country_id.code in (False, "KE")
                and start and move.invoice_date and move.invoice_date >= start)

    def _search_ke_etims_invoice_missing(self, operator, value):
        if operator != "in":
            return NotImplemented
        assert list(value) == [True]
        companies_by_start = defaultdict(list)
        for company in self.env["res.company"].sudo().search(
                [("account_fiscal_country_id.code", "=", "KE"),
                 ("ke_etims_deductible_from", "!=", False)]):
            companies_by_start[company.ke_etims_deductible_from].append(company.id)
        return Domain.AND([
            Domain("move_type", "in", EXPENSE_TYPES),
            Domain("ke_etims_supplier_invoice_ref", "=", False),
            Domain("ke_etims_not_required", "=", False),
            Domain("ke_etims_reverse_invoice", "=", False),
            Domain("commercial_partner_id.ke_etims_no_invoice_needed", "=", False),
            Domain("commercial_partner_id.country_id", "=", False)
            | Domain("commercial_partner_id.country_id.code", "=", "KE"),
            Domain.OR(
                Domain("company_id", "in", company_ids) & Domain("invoice_date", ">=", start)
                for start, company_ids in companies_by_start.items()),
        ])

    @api.depends("ke_etims_invoice_missing")
    def _compute_alerts(self):
        super()._compute_alerts()

    def _get_alerts(self):
        alerts = super()._get_alerts()
        if self.ke_etims_invoice_missing and self.state != "cancel":
            alerts["ke_etims_supplier_invoice_missing"] = {
                "level": "warning",
                "message": _(
                    "No supplier eTIMS invoice number. An expense from %(date)s "
                    "without one is not deductible: ask %(supplier)s for their "
                    "eTIMS invoice, or mark the bill as not needing one.",
                    date=self.company_id.ke_etims_deductible_from,
                    supplier=self.commercial_partner_id.display_name),
            }
        return alerts

    def _ke_etims_number(self):
        """The CU invoice number KRA knows this document by: ours on a sale,
        the supplier's on a bill."""
        self.ensure_one()
        if self.is_purchase_document(include_receipts=True):
            return self.ke_etims_supplier_invoice_ref or False
        return self.ke_etims_cu_invoice or False

    def _ke_etims_numbers(self):
        """Every number KRA may list this document under in the iTax return,
        normalised the way the VAT engine normalises KRA's."""
        self.ensure_one()
        numbers = [self._ke_etims_number()]
        document = self.sudo().ke_etims_document_id
        if not self.is_purchase_document(include_receipts=True) \
                and document.state == "sent" and document.kra_invoice_no:
            numbers.append(str(document.kra_invoice_no))
        return {number.strip().upper() for number in numbers if number and number.strip()}
