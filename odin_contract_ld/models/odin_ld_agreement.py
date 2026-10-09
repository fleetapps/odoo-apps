# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# How the contract expresses the damages rate. Per-day and per-week are the two
# shapes that actually appear in EPC contracts; the percent variants are the
# same arithmetic against contract value instead of a fixed sum.
BASIS = [
    ("per_day", "Fixed amount per day"),
    ("per_week", "Fixed amount per week"),
    ("percent_per_day", "% of contract value per day"),
    ("percent_per_week", "% of contract value per week"),
]

DIRECTION = [
    # We are the contractor and the employer levies damages on us. The charge
    # is deducted from what they pay us, so it lands on a CUSTOMER invoice.
    ("payable", "We owe the customer"),
    # We levy damages on a subcontractor. The charge reduces what we pay them,
    # so it lands on a VENDOR bill.
    ("receivable", "Subcontractor owes us"),
]


class OdinLdAgreement(models.Model):
    _name = "odin.ld.agreement"
    _inherit = ["mail.thread", "mail.activity.mixin", "analytic.mixin"]
    _description = "Liquidated Damages Agreement"
    _order = "date_contractual_completion desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: self.env._("New"),
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        tracking=True,
    )
    project_id = fields.Many2one(
        "project.project",
        string="Project",
        required=True,
        index=True,
        tracking=True,
        domain="[('company_id', 'in', (False, company_id))]",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Counterparty",
        required=True,
        index=True,
        tracking=True,
        help="The employer levying the damages, or the subcontractor they are levied on.",
    )
    direction = fields.Selection(
        DIRECTION,
        required=True,
        default="payable",
        tracking=True,
    )
    contract_ref = fields.Char(string="Contract Reference", tracking=True)
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Sales Order",
        domain="[('partner_id', '=', partner_id)]",
        help="The contract we are performing, when the employer levies damages on us.",
    )
    purchase_order_id = fields.Many2one(
        "purchase.order",
        string="Purchase Order",
        domain="[('partner_id', '=', partner_id)]",
        help="The subcontract, when we levy damages on a subcontractor.",
    )

    # ── The clause ──────────────────────────────────────────────────────────
    contract_value = fields.Monetary(
        required=True,
        tracking=True,
        help="The value the cap is calculated against. Usually the accepted contract amount.",
    )
    basis = fields.Selection(BASIS, required=True, default="per_day", tracking=True)
    rate_amount = fields.Monetary(
        string="Rate",
        tracking=True,
        help="Damages per day or per week, when the clause states a fixed sum.",
    )
    rate_percent = fields.Float(
        string="Rate (%)",
        digits=(16, 4),
        tracking=True,
        help="Damages per period as a percentage of contract value.",
    )
    cap_percent = fields.Float(
        string="Cap (%)",
        default=10.0,
        digits=(16, 4),
        tracking=True,
        help="Maximum damages as a percentage of contract value. 0 means uncapped.",
    )
    cap_amount = fields.Monetary(
        compute="_compute_cap_amount",
        store=True,
        help="0 means uncapped.",
    )
    grace_days = fields.Integer(
        default=0,
        tracking=True,
        help="Days after the completion date before damages begin to run.",
    )

    # ── Dates ───────────────────────────────────────────────────────────────
    date_contractual_completion = fields.Date(
        string="Contractual Completion",
        required=True,
        tracking=True,
    )
    eot_ids = fields.One2many("odin.ld.eot", "agreement_id", string="Extensions of Time")
    eot_days_granted = fields.Integer(
        string="EOT Granted (days)",
        compute="_compute_eot_days_granted",
        store=True,
    )
    date_adjusted_completion = fields.Date(
        string="Adjusted Completion",
        compute="_compute_date_adjusted_completion",
        store=True,
        help="Contractual completion, plus approved extensions of time, plus any grace period.",
    )
    date_actual_completion = fields.Date(
        string="Actual Completion",
        tracking=True,
        help="Leave empty while the works are still running; damages then accrue to today.",
    )

    # ── Accrual ─────────────────────────────────────────────────────────────
    days_delayed = fields.Integer(compute="_compute_accrual", store=True)
    amount_accrued = fields.Monetary(
        compute="_compute_accrual",
        store=True,
        help="Damages earned to date, after the cap.",
    )
    is_capped = fields.Boolean(compute="_compute_accrual", store=True)
    charge_ids = fields.One2many("odin.ld.charge", "agreement_id", string="Charges")
    charge_count = fields.Integer(compute="_compute_charge_count")
    amount_charged = fields.Monetary(
        compute="_compute_charge_totals",
        store=True,
        help="Net damages already applied to an invoice or bill.",
    )
    amount_open = fields.Monetary(
        compute="_compute_charge_totals",
        store=True,
        string="Not Yet Charged",
    )

    account_id = fields.Many2one(
        "account.account",
        string="Damages Account",
        required=True,
        tracking=True,
        domain="[('company_ids', 'in', company_id)]",
        help="Account the deduction line posts to. An expense account when we owe "
        "the damages, an other-income account when we recover them.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("completed", "Completed"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    note = fields.Html(string="Clause Notes", sanitize_attributes=True)

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)",
        "A liquidated damages agreement with this reference already exists.",
    )
    _cap_percent_positive = models.Constraint(
        "CHECK(cap_percent >= 0)",
        "The cap cannot be negative.",
    )
    _grace_days_positive = models.Constraint(
        "CHECK(grace_days >= 0)",
        "The grace period cannot be negative.",
    )

    # ── Computes ────────────────────────────────────────────────────────────
    @api.depends("contract_value", "cap_percent")
    def _compute_cap_amount(self):
        for agreement in self:
            agreement.cap_amount = agreement.currency_id.round(
                agreement.contract_value * agreement.cap_percent / 100.0
            ) if agreement.cap_percent else 0.0

    @api.depends("eot_ids.days_granted", "eot_ids.state")
    def _compute_eot_days_granted(self):
        for agreement in self:
            agreement.eot_days_granted = sum(
                eot.days_granted
                for eot in agreement.eot_ids
                if eot.state == "approved"
            )

    @api.depends("date_contractual_completion", "eot_days_granted", "grace_days")
    def _compute_date_adjusted_completion(self):
        for agreement in self:
            if not agreement.date_contractual_completion:
                agreement.date_adjusted_completion = False
                continue
            agreement.date_adjusted_completion = (
                agreement.date_contractual_completion
                + timedelta(days=agreement.eot_days_granted + agreement.grace_days)
            )

    @api.depends(
        "date_adjusted_completion",
        "date_actual_completion",
        "basis",
        "rate_amount",
        "rate_percent",
        "contract_value",
        "cap_amount",
        "state",
    )
    def _compute_accrual(self):
        today = fields.Date.context_today(self)
        for agreement in self:
            days = 0
            if agreement.date_adjusted_completion and agreement.state not in (
                "draft",
                "cancelled",
            ):
                # While the works run, damages accrue to today. Once completion
                # is recorded, they stop there — not at whenever somebody next
                # opens the record.
                reference = agreement.date_actual_completion or today
                days = max(0, (reference - agreement.date_adjusted_completion).days)
            agreement.days_delayed = days
            raw = agreement._compute_raw_damages(days)
            capped = raw
            if agreement.cap_amount and raw > agreement.cap_amount:
                capped = agreement.cap_amount
            agreement.amount_accrued = agreement.currency_id.round(capped)
            agreement.is_capped = bool(
                agreement.cap_amount and raw > agreement.cap_amount
            )

    def _compute_raw_damages(self, days):
        """Damages before the cap, for a given number of delay days."""
        self.ensure_one()
        if days <= 0:
            return 0.0
        if self.basis == "per_day":
            return days * self.rate_amount
        if self.basis == "per_week":
            # Whole completed weeks only. A contract that says "per week or part
            # thereof" is a different clause; record it as per_day instead of
            # rounding up here, because guessing costs the counterparty money.
            return (days // 7) * self.rate_amount
        if self.basis == "percent_per_day":
            return days * self.contract_value * self.rate_percent / 100.0
        if self.basis == "percent_per_week":
            return (days // 7) * self.contract_value * self.rate_percent / 100.0
        return 0.0

    @api.depends("charge_ids.amount_net", "charge_ids.state", "amount_accrued")
    def _compute_charge_totals(self):
        for agreement in self:
            applied = agreement.charge_ids.filtered(lambda c: c.state == "applied")
            agreement.amount_charged = agreement.currency_id.round(
                sum(applied.mapped("amount_net"))
            )
            agreement.amount_open = agreement.currency_id.round(
                max(0.0, agreement.amount_accrued - agreement.amount_charged)
            )

    @api.depends("charge_ids")
    def _compute_charge_count(self):
        # Separate from the stored totals above: mixing a non-stored field into
        # the same compute makes reading the count able to rewrite them.
        for agreement in self:
            agreement.charge_count = len(agreement.charge_ids)

    # ── Constraints ─────────────────────────────────────────────────────────
    @api.constrains("basis", "rate_amount", "rate_percent")
    def _check_rate(self):
        for agreement in self:
            if agreement.basis in ("per_day", "per_week") and agreement.rate_amount <= 0:
                raise ValidationError(
                    self.env._("Set a damages rate greater than zero.")
                )
            if (
                agreement.basis in ("percent_per_day", "percent_per_week")
                and agreement.rate_percent <= 0
            ):
                raise ValidationError(
                    self.env._("Set a damages percentage greater than zero.")
                )

    @api.constrains("date_contractual_completion", "date_actual_completion")
    def _check_dates(self):
        for agreement in self:
            if (
                agreement.date_actual_completion
                and agreement.date_actual_completion
                < agreement.date_contractual_completion
            ):
                raise ValidationError(
                    self.env._(
                        "Actual completion cannot be earlier than contractual "
                        "completion. If the works finished early there are no "
                        "damages to assess."
                    )
                )

    @api.constrains("direction", "sale_order_id", "purchase_order_id")
    def _check_direction_document(self):
        for agreement in self:
            if agreement.direction == "payable" and agreement.purchase_order_id:
                raise ValidationError(
                    self.env._(
                        "Damages we owe are deducted from a customer invoice, so "
                        "link a sales order rather than a purchase order."
                    )
                )
            if agreement.direction == "receivable" and agreement.sale_order_id:
                raise ValidationError(
                    self.env._(
                        "Damages a subcontractor owes us are deducted from their "
                        "bill, so link a purchase order rather than a sales order."
                    )
                )

    # ── CRUD ────────────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                company_id = vals.get("company_id", self.env.company.id)
                vals["name"] = self.env["ir.sequence"].with_company(
                    company_id
                ).next_by_code("odin.ld.agreement") or self.env._("New")
        return super().create(vals_list)

    @api.onchange("project_id")
    def _onchange_project_id(self):
        if self.project_id:
            if not self.analytic_distribution and self.project_id.account_id:
                self.analytic_distribution = {
                    str(self.project_id.account_id.id): 100.0
                }
            if not self.partner_id:
                self.partner_id = self.project_id.partner_id

    # ── Actions ─────────────────────────────────────────────────────────────
    def action_activate(self):
        for agreement in self:
            if agreement.state != "draft":
                raise UserError(
                    self.env._("Only a draft agreement can be activated.")
                )
        self.write({"state": "active"})
        return True

    def action_complete(self):
        for agreement in self:
            if not agreement.date_actual_completion:
                raise UserError(
                    self.env._(
                        "Record the actual completion date before completing "
                        "%(name)s — it is what fixes the damages.",
                        name=agreement.name,
                    )
                )
        self.write({"state": "completed"})
        return True

    def action_close(self):
        for agreement in self:
            open_charges = agreement.charge_ids.filtered(
                lambda c: c.state in ("draft", "assessed", "approved")
            )
            if open_charges:
                raise UserError(
                    self.env._(
                        "%(count)s charge(s) on %(name)s are still open. Apply, "
                        "waive or cancel them before closing.",
                        count=len(open_charges),
                        name=agreement.name,
                    )
                )
        self.write({"state": "closed"})
        return True

    def action_cancel(self):
        for agreement in self:
            if agreement.charge_ids.filtered(lambda c: c.state == "applied"):
                raise UserError(
                    self.env._(
                        "Damages have already been applied to an invoice under "
                        "%(name)s, so it cannot be cancelled. Close it instead.",
                        name=agreement.name,
                    )
                )
        self.write({"state": "cancelled"})
        return True

    def action_draft(self):
        self.write({"state": "draft"})
        return True

    def action_create_charge(self):
        """Raise a charge for everything accrued but not yet charged."""
        self.ensure_one()
        if self.state not in ("active", "completed"):
            raise UserError(
                self.env._("Activate the agreement before raising a charge.")
            )
        if self.currency_id.is_zero(self.amount_open):
            raise UserError(
                self.env._("There are no uncharged damages on this agreement.")
            )
        last = max(
            self.charge_ids.filtered(lambda c: c.state != "cancelled").mapped("date_to")
            or [self.date_adjusted_completion]
        )
        charge = self.env["odin.ld.charge"].create(
            {
                "agreement_id": self.id,
                "date_from": last,
                "date_to": self.date_actual_completion
                or fields.Date.context_today(self),
                "days_charged": self.days_delayed,
                "amount": self.amount_open,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "odin.ld.charge",
            "res_id": charge.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_charges(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Damages Charges"),
            "res_model": "odin.ld.charge",
            "view_mode": "list,form",
            "domain": [("agreement_id", "=", self.id)],
            "context": {"default_agreement_id": self.id},
        }

    # ── Cron ────────────────────────────────────────────────────────────────
    @api.model
    def _cron_accrue(self):
        """Keep running agreements current, and flag the cap before it bites."""
        agreements = self.search([("state", "=", "active")])
        # amount_accrued depends on today, so a stored compute goes stale
        # overnight. Recompute rather than wait for somebody to open the form.
        agreements.invalidate_recordset(["days_delayed", "amount_accrued", "is_capped"])
        agreements.modified(["date_adjusted_completion"])
        for agreement in agreements:
            if not agreement.cap_amount:
                continue
            ratio = (
                agreement.amount_accrued / agreement.cap_amount
                if agreement.cap_amount
                else 0.0
            )
            if ratio >= 1.0:
                agreement._notify_threshold(
                    self.env._(
                        "Liquidated damages on %(name)s have reached the "
                        "contractual cap of %(cap)s.",
                        name=agreement.name,
                        cap=agreement.cap_amount,
                    )
                )
            elif ratio >= 0.75:
                agreement._notify_threshold(
                    self.env._(
                        "Liquidated damages on %(name)s have passed 75%% of the "
                        "contractual cap.",
                        name=agreement.name,
                    )
                )
        return True

    def _notify_threshold(self, body):
        """One activity per agreement, not one per night."""
        self.ensure_one()
        existing = self.env["mail.activity"].search_count(
            [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("activity_type_id", "=", self.env.ref("mail.mail_activity_data_todo").id),
                ("summary", "=", body[:200]),
            ]
        )
        if existing:
            return
        self.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=body[:200],
            user_id=self.project_id.user_id.id or self.env.uid,
        )
