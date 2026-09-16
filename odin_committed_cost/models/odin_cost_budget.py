# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class OdinCostBudget(models.Model):
    """A project cost budget, broken down by workstream.

    Small on purpose. Odoo Community ships no budgeting at all, and the OCA
    answer (mis_builder_budget) is AGPL, which an OPL-1 app cannot depend on.
    What an EPC contractor needs here is narrow: a figure per workstream, and
    the two other legs of the triangle against it.
    """

    _name = "odin.cost.budget"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Project Cost Budget"
    _order = "date_from desc, id desc"

    name = fields.Char(
        required=True, copy=False, readonly=True,
        default=lambda self: self.env._("New"), index=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(
        "res.currency", required=True,
        default=lambda self: self.env.company.currency_id,
    )
    project_id = fields.Many2one(
        "project.project", required=True, index=True, tracking=True
    )
    date_from = fields.Date(required=True, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    line_ids = fields.One2many("odin.cost.budget.line", "budget_id", copy=True)

    revision_of_id = fields.Many2one(
        "odin.cost.budget", string="Revision Of", readonly=True, copy=False, index="btree_not_null"
    )
    revision_ids = fields.One2many("odin.cost.budget", "revision_of_id", string="Revisions")
    revision_number = fields.Integer(default=0, readonly=True, copy=False)

    amount_budget = fields.Monetary(compute="_compute_totals", store=True)
    amount_committed = fields.Monetary(compute="_compute_totals", store=True)
    amount_actual = fields.Monetary(compute="_compute_totals", store=True)
    amount_consumed = fields.Monetary(compute="_compute_totals", store=True)
    amount_remaining = fields.Monetary(compute="_compute_totals", store=True)
    percent_consumed = fields.Float(compute="_compute_totals", store=True, digits=(16, 2))
    is_overrun = fields.Boolean(compute="_compute_totals", store=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("revised", "Revised"),
            ("closed", "Closed"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    note = fields.Html(sanitize_attributes=True)

    _name_company_uniq = models.Constraint(
        "UNIQUE(name, company_id)", "A budget with this reference already exists."
    )

    @api.depends(
        "line_ids.amount_budget",
        "line_ids.amount_committed",
        "line_ids.amount_actual",
    )
    def _compute_totals(self):
        for budget in self:
            lines = budget.line_ids
            budget.amount_budget = sum(lines.mapped("amount_budget"))
            budget.amount_committed = sum(lines.mapped("amount_committed"))
            budget.amount_actual = sum(lines.mapped("amount_actual"))
            budget.amount_consumed = budget.amount_committed + budget.amount_actual
            budget.amount_remaining = budget.amount_budget - budget.amount_consumed
            budget.percent_consumed = (
                (budget.amount_consumed / budget.amount_budget * 100.0)
                if budget.amount_budget
                else 0.0
            )
            budget.is_overrun = budget.amount_remaining < 0

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for budget in self:
            if budget.date_to < budget.date_from:
                raise ValidationError(
                    self.env._("The budget period ends before it starts.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", self.env._("New")) == self.env._("New"):
                company_id = vals.get("company_id", self.env.company.id)
                vals["name"] = self.env["ir.sequence"].with_company(
                    company_id
                ).next_by_code("odin.cost.budget") or self.env._("New")
        return super().create(vals_list)

    @api.onchange("project_id")
    def _onchange_project_id(self):
        if self.project_id and not self.line_ids and self.project_id.account_id:
            self.line_ids = [
                fields.Command.create(
                    {
                        "name": self.project_id.name,
                        "analytic_account_id": self.project_id.account_id.id,
                    }
                )
            ]

    def action_confirm(self):
        for budget in self:
            if budget.state != "draft":
                raise UserError(self.env._("Only a draft budget can be confirmed."))
            if not budget.line_ids:
                raise UserError(
                    self.env._("A budget with no workstreams measures nothing.")
                )
        self.write({"state": "confirmed"})
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True

    def action_draft(self):
        self.write({"state": "draft"})
        return True

    def action_create_revision(self):
        """Supersede this budget with a copy, keeping the original readable.

        A revised EPC budget is a new document, not an edit. The original has to
        stay as it was, because that is what the original cost report was run
        against.
        """
        self.ensure_one()
        if self.state != "confirmed":
            raise UserError(
                self.env._("Only a confirmed budget can be revised.")
            )
        revision = self.copy(
            {
                "revision_of_id": self.id,
                "revision_number": self.revision_number + 1,
                "state": "draft",
            }
        )
        self.write({"state": "revised"})
        self.message_post(
            body=self.env._(
                "Superseded by revision %(name)s.", name=revision.display_name
            )
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "odin.cost.budget",
            "res_id": revision.id,
            "view_mode": "form",
        }

    def action_refresh(self):
        """Pull committed and actual figures again, on demand."""
        self.line_ids.invalidate_recordset(["amount_committed", "amount_actual"])
        self.line_ids._compute_consumption()
        return True


class OdinCostBudgetLine(models.Model):
    _name = "odin.cost.budget.line"
    _description = "Project Cost Budget Line"
    _order = "sequence, id"

    budget_id = fields.Many2one(
        "odin.cost.budget", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Workstream", required=True)
    company_id = fields.Many2one(related="budget_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="budget_id.currency_id", store=True)
    project_id = fields.Many2one(related="budget_id.project_id", store=True, index=True)
    date_from = fields.Date(related="budget_id.date_from", store=True)
    date_to = fields.Date(related="budget_id.date_to", store=True)
    analytic_account_id = fields.Many2one(
        "account.analytic.account", required=True, index=True, ondelete="restrict"
    )

    amount_budget = fields.Monetary(required=True)
    amount_committed = fields.Monetary(compute="_compute_consumption", store=True)
    amount_actual = fields.Monetary(compute="_compute_consumption", store=True)
    amount_consumed = fields.Monetary(compute="_compute_consumption", store=True)
    amount_remaining = fields.Monetary(compute="_compute_consumption", store=True)
    percent_consumed = fields.Float(
        compute="_compute_consumption", store=True, digits=(16, 2)
    )
    is_overrun = fields.Boolean(compute="_compute_consumption", store=True)

    _account_per_budget_uniq = models.Constraint(
        "UNIQUE(budget_id, analytic_account_id)",
        "That analytic account already has a line on this budget.",
    )

    @api.depends("amount_budget", "analytic_account_id", "date_from", "date_to")
    def _compute_consumption(self):
        """Read committed and actual in two grouped queries, not per line."""
        accounts = self.analytic_account_id
        if not accounts:
            for line in self:
                line.amount_committed = 0.0
                line.amount_actual = 0.0
                line._finalise_consumption()
            return

        # Open commitments, by analytic account.
        commitments = dict(
            self.env["odin.commitment"]._read_group(
                [
                    ("analytic_account_id", "in", accounts.ids),
                    ("state", "=", "open"),
                ],
                groupby=["analytic_account_id"],
                aggregates=["amount_open:sum"],
            )
        )

        # Actuals. Two things to get right here.
        #
        # Sign: analytic amounts are NEGATIVE for costs, because
        # account_move_line._prepare_analytic_distribution_line computes
        # amount = -balance. The sum is inverted to give cost as a positive.
        #
        # Scope: category is 'invoice' on a sale document, 'vendor_bill' on a
        # purchase one and 'other' for timesheets and expenses. Revenue is
        # excluded, so a project's own billing does not read as negative cost.
        actuals = dict(
            self.env["account.analytic.line"]._read_group(
                [
                    ("account_id", "in", accounts.ids),
                    ("category", "!=", "invoice"),
                ],
                groupby=["account_id"],
                aggregates=["amount:sum"],
            )
        )

        for line in self:
            account = line.analytic_account_id
            line.amount_committed = commitments.get(account, 0.0)
            line.amount_actual = -actuals.get(account, 0.0)
            line._finalise_consumption()

    def _finalise_consumption(self):
        self.ensure_one()
        self.amount_consumed = self.amount_committed + self.amount_actual
        self.amount_remaining = self.amount_budget - self.amount_consumed
        self.percent_consumed = (
            (self.amount_consumed / self.amount_budget * 100.0)
            if self.amount_budget
            else 0.0
        )
        self.is_overrun = self.amount_remaining < 0

    def action_view_commitments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Commitments"),
            "res_model": "odin.commitment",
            "view_mode": "list,form",
            "domain": [
                ("analytic_account_id", "=", self.analytic_account_id.id),
                ("state", "=", "open"),
            ],
        }

    def action_view_actuals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Actual Costs"),
            "res_model": "account.analytic.line",
            "view_mode": "list,form",
            "domain": [
                ("account_id", "=", self.analytic_account_id.id),
                ("category", "!=", "invoice"),
            ],
        }
