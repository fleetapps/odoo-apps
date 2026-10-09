"""Financial budgets typed straight into the P&L, as Odoo 19 Enterprise does:
pick or name a budget, and a Budget column and a % column appear next to the
balance; an account's budget is entered in its cell.

https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting/budget.html
("Financial budgets ... Assign amounts to each account requiring analysis.")

Community has no such model (``crossovered.budget`` from third-party add-ons
budgets analytic accounts, not ledger accounts), so this one is ours. Amounts
are stored per account and per month, with the sign of a balance (credit
negative), so that layouts can be changed without the budget changing
meaning. The report converts them to each line's displayed sign.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class OdinPnlBudget(models.Model):
    _name = "odin.pnl.budget"
    _description = "P&L budget"
    _order = "date_from desc, name, id"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    line_ids = fields.One2many("odin.pnl.budget.line", "budget_id", string="Amounts", copy=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for budget in self:
            if budget.date_to < budget.date_from:
                raise ValidationError(_("A budget cannot end before it starts."))


class OdinPnlBudgetLine(models.Model):
    _name = "odin.pnl.budget.line"
    _description = "P&L budget amount"
    _order = "date, account_id"

    budget_id = fields.Many2one("odin.pnl.budget", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="budget_id.company_id", store=True, index=True)
    account_id = fields.Many2one("account.account", required=True, ondelete="cascade", index=True)
    date = fields.Date(required=True, help="The first day of the month this amount is for.")
    balance = fields.Monetary(
        currency_field="currency_id",
        help="Signed like a journal item: income budgets are negative (credit).")
    currency_id = fields.Many2one(related="company_id.currency_id")

    _account_month_uniq = models.Constraint(
        "UNIQUE(budget_id, account_id, date)",
        "A budget holds one amount per account and month.")

    @api.constrains("date")
    def _check_first_of_month(self):
        for line in self:
            if line.date.day != 1:
                raise ValidationError(_("Budget amounts are entered per month, on its first day."))
