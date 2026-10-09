# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    odin_budget_ids = fields.One2many(
        "odin.cost.budget", "project_id", string="Cost Budgets"
    )
    odin_budget_count = fields.Integer(compute="_compute_odin_cost_totals")
    odin_amount_budget = fields.Monetary(
        string="Budget", compute="_compute_odin_cost_totals", currency_field="currency_id"
    )
    odin_amount_committed = fields.Monetary(
        string="Committed", compute="_compute_odin_cost_totals", currency_field="currency_id"
    )
    odin_amount_actual = fields.Monetary(
        string="Actual Cost", compute="_compute_odin_cost_totals", currency_field="currency_id"
    )
    odin_amount_remaining = fields.Monetary(
        string="Budget Remaining",
        compute="_compute_odin_cost_totals",
        currency_field="currency_id",
        help="Budget less committed less actual. Negative means the project is "
        "already overspent once open orders are counted.",
    )

    @api.depends(
        "odin_budget_ids.state",
        "odin_budget_ids.amount_budget",
        "odin_budget_ids.amount_committed",
        "odin_budget_ids.amount_actual",
    )
    def _compute_odin_cost_totals(self):
        for project in self:
            budgets = project.odin_budget_ids
            project.odin_budget_count = len(budgets)
            live = budgets.filtered(lambda b: b.state == "confirmed")
            project.odin_amount_budget = sum(live.mapped("amount_budget"))
            project.odin_amount_committed = sum(live.mapped("amount_committed"))
            project.odin_amount_actual = sum(live.mapped("amount_actual"))
            project.odin_amount_remaining = sum(live.mapped("amount_remaining"))

    def action_view_odin_budgets(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Cost Budgets"),
            "res_model": "odin.cost.budget",
            "view_mode": "list,form",
            "domain": [("project_id", "=", self.id)],
            "context": {"default_project_id": self.id},
        }

    def action_view_odin_cost_control(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Cost Control"),
            "res_model": "odin.cost.control.report",
            "view_mode": "pivot,graph,list",
            "domain": [("project_id", "=", self.id)],
        }
