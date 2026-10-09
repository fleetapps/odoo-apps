# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import UserError


class OdinLcAmendWizard(models.TransientModel):
    _name = "odin.lc.amend.wizard"
    _description = "Request a Letter of Credit Amendment"

    lc_id = fields.Many2one("odin.letter.of.credit", required=True, readonly=True)
    currency_id = fields.Many2one(related="lc_id.currency_id")
    amendment_type = fields.Selection(
        selection=lambda self: self.env["odin.lc.amendment"]._fields[
            "amendment_type"
        ].selection,
        required=True,
        default="expiry",
    )
    description = fields.Text(required=True)
    bank_reference = fields.Char(string="Bank Amendment Ref")

    amount_current = fields.Monetary(related="lc_id.amount", string="Current Amount")
    amount_new = fields.Monetary(string="New Amount")
    date_current = fields.Date(compute="_compute_date_current", string="Current Date")
    date_new = fields.Date(string="New Date")

    @api.depends("lc_id", "amendment_type")
    def _compute_date_current(self):
        for wizard in self:
            if wizard.amendment_type == "expiry":
                wizard.date_current = wizard.lc_id.date_expiry
            elif wizard.amendment_type == "shipment":
                wizard.date_current = wizard.lc_id.date_latest_shipment
            else:
                wizard.date_current = False

    def action_create_amendment(self):
        self.ensure_one()
        if self.amendment_type == "amount" and not self.amount_new:
            raise UserError(self.env._("Enter the amended amount."))
        if self.amendment_type in ("expiry", "shipment") and not self.date_new:
            raise UserError(self.env._("Enter the amended date."))
        amendment = self.env["odin.lc.amendment"].create(
            {
                "lc_id": self.lc_id.id,
                "amendment_type": self.amendment_type,
                "description": self.description,
                "bank_reference": self.bank_reference,
                "amount_new": self.amount_new,
                "date_new": self.date_new,
            }
        )
        amendment.action_request()
        return {
            "type": "ir.actions.act_window",
            "res_model": "odin.lc.amendment",
            "res_id": amendment.id,
            "view_mode": "form",
        }
