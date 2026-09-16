# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class OdinLdEot(models.Model):
    _name = "odin.ld.eot"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Extension of Time Claim"
    _order = "date_claim desc, id desc"

    name = fields.Char(
        string="Claim Reference",
        required=True,
        copy=False,
        default=lambda self: self.env._("New"),
    )
    agreement_id = fields.Many2one(
        "odin.ld.agreement",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="agreement_id.company_id", store=True)
    project_id = fields.Many2one(related="agreement_id.project_id", store=True)
    description = fields.Text(required=True, help="The event relied on, and its effect on the programme.")
    date_claim = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_decision = fields.Date(tracking=True, readonly=True, copy=False)
    days_requested = fields.Integer(required=True, tracking=True)
    days_granted = fields.Integer(
        tracking=True,
        readonly=True,
        copy=False,
        help="Only approved claims move the completion date.",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Decided By",
        readonly=True,
        copy=False,
        tracking=True,
    )
    decision_note = fields.Text(readonly=True, copy=False)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )

    _days_requested_positive = models.Constraint(
        "CHECK(days_requested > 0)",
        "An extension of time must request at least one day.",
    )
    _days_granted_positive = models.Constraint(
        "CHECK(days_granted >= 0)",
        "Days granted cannot be negative.",
    )

    @api.constrains("days_granted", "days_requested", "state")
    def _check_days_granted(self):
        for eot in self:
            if eot.state == "approved" and eot.days_granted > eot.days_requested:
                raise ValidationError(
                    self.env._(
                        "Granting more days than were claimed is almost always a "
                        "data-entry error. Amend the claim instead."
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "odin.ld.eot"
                ) or self.env._("New")
        return super().create(vals_list)

    def action_submit(self):
        for eot in self:
            if eot.state != "draft":
                raise UserError(self.env._("Only a draft claim can be submitted."))
        self.write({"state": "submitted"})
        return True

    def action_approve(self):
        """Approve for the days claimed. Use the form to grant fewer."""
        for eot in self:
            if eot.state != "submitted":
                raise UserError(
                    self.env._("Only a submitted claim can be decided.")
                )
            eot.write(
                {
                    "state": "approved",
                    "days_granted": eot.days_granted or eot.days_requested,
                    "date_decision": fields.Date.context_today(eot),
                    "user_id": self.env.uid,
                }
            )
        return True

    def action_reject(self):
        for eot in self:
            if eot.state != "submitted":
                raise UserError(
                    self.env._("Only a submitted claim can be decided.")
                )
        self.write(
            {
                "state": "rejected",
                "days_granted": 0,
                "date_decision": fields.Date.context_today(self),
                "user_id": self.env.uid,
            }
        )
        return True

    def action_draft(self):
        self.write(
            {
                "state": "draft",
                "days_granted": 0,
                "date_decision": False,
                "user_id": False,
            }
        )
        return True
