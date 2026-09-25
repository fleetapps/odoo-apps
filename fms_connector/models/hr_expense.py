# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""Extends hr.expense with the FMS reference fields and the upsert logic
the controller calls once a job card is signed and the vendor invoice is
in FMS.

Field names verified against
https://github.com/odoo/odoo/blob/19.0/addons/hr_expense/models/hr_expense.py
on 2026-09-23: ``employee_id`` (required), ``product_id`` ("Category",
domain can_be_expensed=True), ``total_amount_currency`` (Monetary, the one
to set -- price_unit/quantity are computed from it), ``currency_id``
(required), ``state`` (draft/submitted/approved/posted/in_payment/paid/
refused), ``attachment_ids`` (One2many ir.attachment, res_model='hr.expense').

A bare ``create()`` only ever reaches ``state == 'draft'`` on either
version -- see ``_fms_submit_for_approval()`` below for the (materially
different, version-specific) step actually needed to put the expense in
front of an approver.

Deliberately NOT set: any technical labor-hour/rate breakdown -- FMS sends
one total per the customer's own requirement ("no technical no hours or
rate"), so this is always a single expense line, quantity 1.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HrExpense(models.Model):
    _inherit = "hr.expense"

    x_fms_ref = fields.Char(
        string="FMS Reference", copy=False, index=True,
        help="FMS work_orders/job card id (UUID). Used to upsert instead "
             "of duplicating on a retried push.",
    )
    x_fms_purchase_request_ref = fields.Char(
        string="FMS Purchase Request Reference",
        help="Links back to the purchase.request this expense settles, "
             "when the job card originated from one.",
    )
    x_fms_vehicle_ref = fields.Char(string="FMS Vehicle Ref")

    _sql_fms_ref_unique = models.Constraint(
        "UNIQUE(x_fms_ref)",
        "This FMS job card has already been pushed to Odoo as an expense.",
    )

    @api.model
    def _fms_get_config(self, key, required=False):
        value = self.env["ir.config_parameter"].sudo().get_param(
            "fms_connector." + key
        )
        if required and not value:
            raise UserError(_(
                "fms_connector is not fully configured: missing '%s'. "
                "Set it under Settings > Fleet FMS Connector."
            ) % key)
        return value

    @api.model
    def _fms_resolve_employee_by_email(self, email):
        employee = self.env["hr.employee"]
        if email:
            employee = self.env["hr.employee"].search(
                ["|", ("work_email", "=ilike", email),
                 ("user_id.login", "=ilike", email)],
                limit=1,
            )
        if not employee:
            fallback_id = self._fms_get_config("default_expense_employee_id")
            if fallback_id:
                employee = self.env["hr.employee"].browse(
                    int(fallback_id)
                ).exists()
        return employee

    @api.model
    def fms_upsert(self, payload):
        """Create or update an hr.expense from an FMS payload dict.
        Idempotent on ``x_fms_ref``. Expected keys: fms_ref,
        purchase_request_fms_ref, employee_email, employee_name,
        vehicle_ref, vendor_name, description, amount, currency,
        expense_date, attachment_filename, attachment_base64."""
        fms_ref = payload.get("fms_ref")
        if not fms_ref:
            raise UserError(_("Missing required field: fms_ref"))

        existing = self.search([("x_fms_ref", "=", fms_ref)], limit=1)
        if existing and existing.state != "draft":
            return existing

        employee = self._fms_resolve_employee_by_email(
            payload.get("employee_email")
        )
        if not employee:
            raise UserError(_(
                "No Odoo employee matches '%s' and no fallback expense "
                "employee is configured (Settings > Fleet FMS Connector)."
            ) % payload.get("employee_email"))

        product_id = self._fms_get_config(
            "default_expense_product_id", required=True
        )
        currency = self.env["res.currency"].search(
            [("name", "=", payload.get("currency"))], limit=1
        ) or self.env.company.currency_id

        description = payload.get("description") or _("Vehicle maintenance/repair")
        if payload.get("vendor_name"):
            description = f"{description} — {payload['vendor_name']}"

        vals = {
            "employee_id": employee.id,
            "product_id": int(product_id),
            "name": description,
            "total_amount_currency": payload.get("amount") or 0.0,
            "currency_id": currency.id,
            "date": payload.get("expense_date") or fields.Date.today(),
            "quantity": 1,
            "payment_mode": "company_account",
            "x_fms_ref": fms_ref,
            "x_fms_purchase_request_ref": payload.get("purchase_request_fms_ref"),
            "x_fms_vehicle_ref": payload.get("vehicle_ref"),
        }

        if existing:
            existing.write(vals)
            expense = existing
        else:
            expense = self.create(vals)

        attachment_b64 = payload.get("attachment_base64")
        if attachment_b64:
            self.env["ir.attachment"].create({
                "name": payload.get("attachment_filename") or "invoice.pdf",
                "datas": attachment_b64
                if isinstance(attachment_b64, bytes)
                else attachment_b64.encode(),
                "res_model": "hr.expense",
                "res_id": expense.id,
            })

        expense._fms_submit_for_approval()
        return expense

    def _fms_submit_for_approval(self):
        """A bare create() leaves the expense as an invisible personal
        draft on BOTH versions -- nobody is notified and there is nothing
        for Rose to approve until it is actually submitted. Two different
        mechanisms depending on version, confirmed against the actual
        model source on 2026-09-23 (not just the functional docs):

        - Odoo 19 removed hr.expense.sheet entirely and consolidated the
          workflow onto hr.expense itself: action_submit() moves it to
          'submitted' (or straight to 'approved' if the employee has no
          expense manager configured -- see _can_be_autovalidated(), an
          Odoo policy this deliberately does not try to override).
          https://github.com/odoo/odoo/blob/19.0/addons/hr_expense/models/hr_expense.py
        - Odoo 18 computes `state` purely from a linked hr.expense.sheet
          (state stays 'draft' forever without one -- there is no direct
          action on hr.expense itself); wrap it in a single-expense sheet
          and submit that.
          https://github.com/odoo/odoo/blob/18.0/addons/hr_expense/models/hr_expense_sheet.py

        action_submit()'s own permission check (`user.employee_id !=
        expense.employee_id and not expense.can_approve`) does not block
        this even though the controller runs as a public/API user with no
        employee record: can_approve's compute explicitly treats
        self.env.su (True under our .sudo() call chain) as sufficient,
        same as any other superuser-mode bypass.
        """
        self.ensure_one()
        if self.state != "draft":
            return
        if hasattr(self, "action_submit"):
            self.action_submit()
        else:
            sheet = self.env["hr.expense.sheet"].create({
                "name": self.name,
                "employee_id": self.employee_id.id,
                "expense_line_ids": [(6, 0, self.ids)],
            })
            sheet.action_submit_sheet()

    def fms_status_payload(self):
        self.ensure_one()
        return {
            "id": self.id,
            "state": self.state,
            "url": self._fms_record_url(),
        }

    def _fms_record_url(self):
        """See purchase_request.py: never bare 'web.base.url' for an
        outbound link -- it gets rewritten by whoever last logged into the
        backend."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("fms_connector.public_base_url") or icp.get_param(
            "web.base.url", ""
        )
        return f"{base_url}/web#model=hr.expense&view_type=form&id={self.id}"
