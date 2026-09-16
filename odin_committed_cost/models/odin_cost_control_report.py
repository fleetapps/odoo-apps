# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models
from odoo.tools import SQL


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

    @property
    def _table_query(self):
        return self._query()

    def _query(self):
        return SQL(
            """
            SELECT ROW_NUMBER() OVER (ORDER BY sub.kind, sub.date, sub.analytic_account_id) AS id,
                   sub.*
              FROM (
                    -- Budget: confirmed budgets only. A draft budget is a
                    -- proposal and must not appear in a control report.
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

                     UNION ALL

                    -- Committed: what is still open on confirmed orders.
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

                     UNION ALL

                    -- Actual: analytic amounts are negative for costs, so they
                    -- are inverted. Sale documents are excluded so a project's
                    -- own revenue does not read as negative cost.
                    SELECT 'actual',
                           al.account_id AS analytic_account_id,
                           p.id AS project_id,
                           al.partner_id,
                           al.company_id,
                           co.currency_id,
                           al.date,
                           -al.amount
                      FROM account_analytic_line al
                      JOIN res_company co ON co.id = al.company_id
                 LEFT JOIN project_project p ON p.account_id = al.account_id
                     WHERE al.category IS DISTINCT FROM 'invoice'
              ) sub
            """
        )
