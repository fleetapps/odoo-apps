# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""Allocate an unallocated receipt by hand, or park it with a reason."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MpesaAllocate(models.TransientModel):
    _name = "mpesa.allocate"
    _description = "Allocate M-Pesa Receipt"

    transaction_ids = fields.Many2many("mpesa.transaction", string="Receipts", required=True)
    company_id = fields.Many2one("res.company", compute="_compute_company")
    currency_id = fields.Many2one(related="company_id.currency_id")
    amount = fields.Monetary(compute="_compute_company")
    mode = fields.Selection(
        [("invoice", "Pay invoices"), ("partner", "Customer on account"),
         ("park", "Park in suspense")],
        required=True, default="invoice")
    partner_id = fields.Many2one("res.partner", string="Customer")
    invoice_ids = fields.Many2many(
        "account.move", string="Invoices",
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), "
               "('payment_state', 'in', ('not_paid', 'partial')), "
               "('company_id', '=', company_id)]")
    reason = fields.Char()

    @api.depends("transaction_ids")
    def _compute_company(self):
        for wizard in self:
            wizard.company_id = wizard.transaction_ids.company_id[:1]
            wizard.amount = sum(wizard.transaction_ids.mapped("amount"))

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id and self.mode == "invoice":
            self.invoice_ids = self.env["mpesa.transaction"]._open_invoices(self.partner_id)

    def action_confirm(self):
        self.ensure_one()
        if self.mode == "park":
            self.transaction_ids.action_park(self.reason)
            return {"type": "ir.actions.act_window_close"}
        if len(self.transaction_ids) != 1:
            raise UserError(_("Allocate one receipt at a time."))
        tx = self.transaction_ids
        if self.mode == "invoice":
            if not self.invoice_ids:
                raise UserError(_("Choose the invoices to pay."))
            tx._allocate_manual(invoices=self.invoice_ids.sorted(
                lambda m: (m.invoice_date_due or m.invoice_date or m.date, m.id)),
                partner=self.partner_id or self.invoice_ids.commercial_partner_id[:1])
        else:
            if not self.partner_id:
                raise UserError(_("Choose the customer."))
            tx._allocate_manual(partner=self.partner_id)
        return {"type": "ir.actions.act_window_close"}
