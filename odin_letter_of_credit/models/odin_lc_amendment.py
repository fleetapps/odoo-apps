# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import UserError

AMENDMENT_TYPE = [
    ("amount", "Amount"),
    ("expiry", "Expiry Date"),
    ("shipment", "Latest Shipment Date"),
    ("documents", "Document Set"),
    ("other", "Other Terms"),
]


class OdinLcAmendment(models.Model):
    """An amendment to an issued credit.

    Accepting one writes the new term through to the credit, so the credit
    always shows what is currently in force while the amendment history shows
    how it got there.
    """

    _name = "odin.lc.amendment"
    _inherit = ["mail.thread"]
    _description = "Letter of Credit Amendment"
    _order = "date desc, id desc"

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: self.env._("New"),
    )
    lc_id = fields.Many2one(
        "odin.letter.of.credit", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="lc_id.company_id", store=True)
    currency_id = fields.Many2one(related="lc_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    bank_reference = fields.Char(string="Bank Amendment Ref", tracking=True)
    amendment_type = fields.Selection(AMENDMENT_TYPE, required=True, tracking=True)
    description = fields.Text(required=True)

    amount_old = fields.Monetary(readonly=True)
    amount_new = fields.Monetary()
    date_old = fields.Date(readonly=True)
    date_new = fields.Date()

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("requested", "Requested"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "odin.lc.amendment"
                ) or self.env._("New")
        return super().create(vals_list)

    def action_request(self):
        for amendment in self:
            if amendment.state != "draft":
                raise UserError(self.env._("Only a draft amendment can be requested."))
            # Snapshot what is being changed FROM, at the moment of asking, so
            # the history is readable after several amendments.
            lc = amendment.lc_id
            if amendment.amendment_type == "amount":
                amendment.amount_old = lc.amount
            elif amendment.amendment_type == "expiry":
                amendment.date_old = lc.date_expiry
            elif amendment.amendment_type == "shipment":
                amendment.date_old = lc.date_latest_shipment
        self.write({"state": "requested"})
        return True

    def action_accept(self):
        for amendment in self:
            if amendment.state != "requested":
                raise UserError(
                    self.env._("Only a requested amendment can be accepted.")
                )
            lc = amendment.lc_id
            if amendment.amendment_type == "amount":
                if not amendment.amount_new:
                    raise UserError(
                        self.env._("Enter the amended amount before accepting.")
                    )
                lc.amount = amendment.amount_new
            elif amendment.amendment_type == "expiry":
                if not amendment.date_new:
                    raise UserError(
                        self.env._("Enter the amended expiry date before accepting.")
                    )
                lc.date_expiry = amendment.date_new
                # An amendment that extends an expired credit revives it.
                if lc.state == "expired" and amendment.date_new >= fields.Date.context_today(lc):
                    lc.state = "advised" if lc.date_issue else "issued"
            elif amendment.amendment_type == "shipment":
                lc.date_latest_shipment = amendment.date_new
            amendment.state = "accepted"
            lc.message_post(
                body=self.env._(
                    "Amendment %(ref)s accepted: %(desc)s",
                    ref=amendment.name,
                    desc=amendment.description,
                )
            )
        return True

    def action_reject(self):
        for amendment in self:
            if amendment.state != "requested":
                raise UserError(
                    self.env._("Only a requested amendment can be rejected.")
                )
        self.write({"state": "rejected"})
        return True
