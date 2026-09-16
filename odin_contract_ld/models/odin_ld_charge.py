# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class OdinLdCharge(models.Model):
    _name = "odin.ld.charge"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Liquidated Damages Charge"
    _order = "date desc, id desc"

    name = fields.Char(
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: self.env._("New"),
        index=True,
    )
    agreement_id = fields.Many2one(
        "odin.ld.agreement",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(related="agreement_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="agreement_id.currency_id", store=True)
    partner_id = fields.Many2one(related="agreement_id.partner_id", store=True, index=True)
    project_id = fields.Many2one(related="agreement_id.project_id", store=True, index=True)
    direction = fields.Selection(related="agreement_id.direction", store=True)
    account_id = fields.Many2one(related="agreement_id.account_id", readonly=False)
    analytic_distribution = fields.Json(related="agreement_id.analytic_distribution", readonly=False)

    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_from = fields.Date(string="Period From", required=True)
    date_to = fields.Date(string="Period To", required=True)
    days_charged = fields.Integer(tracking=True)

    amount = fields.Monetary(
        string="Assessed",
        required=True,
        tracking=True,
        help="Damages assessed for this period, before any waiver.",
    )
    amount_waived = fields.Monetary(
        string="Waived",
        tracking=True,
        help="Part of the assessment given up commercially. Recorded rather than "
        "deleted, so the concession stays visible.",
    )
    amount_net = fields.Monetary(
        string="Net Charge",
        compute="_compute_amount_net",
        store=True,
        tracking=True,
    )
    waiver_reason = fields.Text(tracking=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("assessed", "Assessed"),
            ("approved", "Approved"),
            ("applied", "Applied"),
            ("waived", "Waived"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    move_id = fields.Many2one(
        "account.move",
        string="Applied To",
        readonly=True,
        copy=False,
        tracking=True,
        index=True,
    )
    move_line_id = fields.Many2one(
        "account.move.line",
        string="Deduction Line",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    move_state = fields.Selection(related="move_id.state")
    user_id = fields.Many2one(
        "res.users", string="Approved By", readonly=True, copy=False, tracking=True
    )
    date_approved = fields.Date(readonly=True, copy=False)
    note = fields.Text()

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)",
        "A damages charge with this reference already exists.",
    )
    _amount_positive = models.Constraint(
        "CHECK(amount >= 0)",
        "An assessed amount cannot be negative.",
    )
    _waived_positive = models.Constraint(
        "CHECK(amount_waived >= 0)",
        "A waived amount cannot be negative.",
    )

    @api.depends("amount", "amount_waived")
    def _compute_amount_net(self):
        for charge in self:
            charge.amount_net = max(0.0, charge.amount - charge.amount_waived)

    @api.constrains("amount", "amount_waived")
    def _check_waiver(self):
        for charge in self:
            if charge.amount_waived > charge.amount:
                raise ValidationError(
                    self.env._("You cannot waive more than the amount assessed.")
                )

    @api.constrains("date_from", "date_to")
    def _check_period(self):
        for charge in self:
            if charge.date_to < charge.date_from:
                raise ValidationError(
                    self.env._("The period end cannot be before the period start.")
                )

    @api.constrains("amount", "agreement_id", "state")
    def _check_against_cap(self):
        """The cap is a contractual ceiling on the total, not on one charge."""
        for charge in self:
            agreement = charge.agreement_id
            if not agreement.cap_amount or charge.state in ("cancelled", "waived"):
                continue
            other = agreement.charge_ids.filtered(
                lambda c, cur=charge: c.id != cur.id and c.state == "applied"
            )
            total = sum(other.mapped("amount_net")) + charge.amount_net
            if agreement.currency_id.compare_amounts(total, agreement.cap_amount) > 0:
                raise ValidationError(
                    self.env._(
                        "This charge would take total damages on %(name)s to "
                        "%(total)s, above the contractual cap of %(cap)s.",
                        name=agreement.name,
                        total=total,
                        cap=agreement.cap_amount,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                company_id = vals.get("company_id") or self.env.company.id
                vals["name"] = self.env["ir.sequence"].with_company(
                    company_id
                ).next_by_code("odin.ld.charge") or self.env._("New")
        return super().create(vals_list)

    def action_assess(self):
        for charge in self:
            if charge.state != "draft":
                raise UserError(self.env._("Only a draft charge can be assessed."))
        self.write({"state": "assessed"})
        return True

    def action_approve(self):
        for charge in self:
            if charge.state != "assessed":
                raise UserError(
                    self.env._("Only an assessed charge can be approved.")
                )
        self.write(
            {
                "state": "approved",
                "user_id": self.env.uid,
                "date_approved": fields.Date.context_today(self),
            }
        )
        return True

    def action_waive(self):
        """Give up the whole charge, keeping the record of having done so."""
        for charge in self:
            if charge.state == "applied":
                raise UserError(
                    self.env._(
                        "%(name)s is already on a posted document. Credit the "
                        "document rather than waiving the charge.",
                        name=charge.name,
                    )
                )
            if not charge.waiver_reason:
                raise UserError(
                    self.env._(
                        "Record why %(name)s is being waived — a concession with "
                        "no stated reason is indistinguishable from an error.",
                        name=charge.name,
                    )
                )
            charge.write({"amount_waived": charge.amount, "state": "waived"})
        return True

    def action_cancel(self):
        for charge in self:
            if charge.state == "applied":
                raise UserError(
                    self.env._(
                        "%(name)s has been applied to %(move)s. Reverse it there "
                        "first.",
                        name=charge.name,
                        move=charge.move_id.display_name,
                    )
                )
        self.write({"state": "cancelled"})
        return True

    def action_draft(self):
        for charge in self:
            if charge.state == "applied":
                raise UserError(
                    self.env._("Detach the charge from its document first.")
                )
        self.write({"state": "draft", "user_id": False, "date_approved": False})
        return True

    def action_open_apply_wizard(self):
        self.ensure_one()
        if self.state != "approved":
            raise UserError(
                self.env._(
                    "Approve %(name)s before deducting it from a document.",
                    name=self.name,
                )
            )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Deduct Liquidated Damages"),
            "res_model": "odin.ld.apply.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_charge_id": self.id},
        }

    def action_view_move(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(self.env._("This charge has not been applied yet."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }

    def action_detach(self):
        """Unhook a charge from a document that is still in draft."""
        for charge in self:
            if charge.move_id and charge.move_id.state != "draft":
                raise UserError(
                    self.env._(
                        "%(move)s is no longer a draft, so the deduction cannot "
                        "simply be removed.",
                        move=charge.move_id.display_name,
                    )
                )
            charge.move_line_id.unlink()
            charge.write({"move_id": False, "move_line_id": False, "state": "approved"})
        return True
