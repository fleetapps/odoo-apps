# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL
from odoo.tools.sql import column_exists


class OdinCostControlReport(models.Model):
    """Budget, committed and actual in one place, for pivoting.

    A database view rather than three separate models, because the question
    users ask — "where is this project against budget?" — needs all three legs
    side by side, grouped however they like.
    """

    _name = "odin.cost.control.report"
    _description = "Cost Control Analysis"
    _auto = False
    _order = "date desc"
    _rec_name = "analytic_account_id"

    kind = fields.Selection(
        [
            ("budget", "Budget"),
            ("committed", "Committed"),
            ("actual", "Actual"),
        ],
        readonly=True,
    )
    analytic_account_id = fields.Many2one(
        "account.analytic.account", string="Analytic Account", readonly=True
    )
    project_id = fields.Many2one("project.project", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Vendor", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    date = fields.Date(readonly=True)
    amount = fields.Monetary(readonly=True)

    # A _table_query model reads tables the ORM does not know it depends on, so
    # nothing flushes them before the query runs. Without this the report is
    # blind to anything written in the current transaction — which is every
    # test, and any user action that reads the report straight after saving.
    _SOURCE_MODELS = (
        "odin.cost.budget",
        "odin.cost.budget.line",
        "odin.commitment",
        "account.analytic.line",
    )

    def _flush_sources(self):
        for model in self._SOURCE_MODELS:
            self.env[model].flush_model()

    def _search(self, *args, **kwargs):
        self._flush_sources()
        return super()._search(*args, **kwargs)

    def _read_group(self, *args, **kwargs):
        self._flush_sources()
        return super()._read_group(*args, **kwargs)

    @property
    def _table_query(self):
        return self._query()

    def _analytic_columns(self):
        """The column each analytic plan stores its account in.

        account.analytic.line does not keep every plan in account_id. Only the
        designated project plan does; every other root plan gets its own column,
        x_plan<id>_id — see analytic_plan._strict_column_name. A report that
        reads account_id alone is simply blind to every other plan.
        """
        Plan = self.env["account.analytic.plan"]
        try:
            project_plan, other_plans = Plan._get_all_plans()
            plans = project_plan | other_plans
        except UserError:
            # No project plan configured yet; account_id is all there is.
            return ["account_id"]
        columns = []
        for plan in plans:
            column = plan._strict_column_name()
            # A plan whose column has not been synced onto the table yet would
            # make the whole report error rather than miss one plan.
            if column == "account_id" or column_exists(
                self.env.cr, "account_analytic_line", column
            ):
                columns.append(column)
        return columns or ["account_id"]

    def _actual_branches(self):
        branches = []
        for column in self._analytic_columns():
            identifier = SQL.identifier(column)
            branches.append(
                SQL(
                    """
                    SELECT 'actual' AS kind,
                           al.%s AS analytic_account_id,
                           p.id AS project_id,
                           al.partner_id,
                           al.company_id,
                           co.currency_id,
                           al.date,
                           -al.amount AS amount
                      FROM account_analytic_line al
                      JOIN res_company co ON co.id = al.company_id
                 LEFT JOIN project_project p ON p.account_id = al.%s
                     WHERE al.%s IS NOT NULL
                       AND al.category IS DISTINCT FROM 'invoice'
                    """,
                    identifier,
                    identifier,
                    identifier,
                )
            )
        return branches

    def _query(self):
        branches = [
            # Budget: confirmed budgets only. A draft budget is a proposal and
            # must not appear in a control report.
            SQL(
                """
                SELECT 'budget' AS kind,
                       bl.analytic_account_id,
                       b.project_id,
                       NULL::integer AS partner_id,
                       b.company_id,
                       b.currency_id,
                       b.date_from AS date,
                       bl.amount_budget AS amount
                  FROM odin_cost_budget_line bl
                  JOIN odin_cost_budget b ON b.id = bl.budget_id
                 WHERE b.state = 'confirmed'
                """
            ),
            # Committed: what is still open on confirmed orders.
            SQL(
                """
                SELECT 'committed',
                       c.analytic_account_id,
                       c.project_id,
                       c.partner_id,
                       c.company_id,
                       c.currency_id,
                       c.date,
                       c.amount_open
                  FROM odin_commitment c
                 WHERE c.state = 'open'
                """
            ),
            # Actual: one branch per analytic plan column. Amounts are negative
            # for costs, so they are inverted; sale documents are excluded so a
            # project's own revenue does not read as negative cost.
            *self._actual_branches(),
        ]
        return SQL(
            """
            SELECT ROW_NUMBER() OVER (ORDER BY sub.kind, sub.date, sub.analytic_account_id) AS id,
                   sub.*
              FROM (%s) sub
            """,
            SQL(" UNION ALL ").join(branches),
        )
