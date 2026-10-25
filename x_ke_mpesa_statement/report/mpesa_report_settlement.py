# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models, tools


class MpesaReportSettlement(models.Model):
    """Report 8: what each till received, paid in charges and swept to the
    bank, per month, from its statement lines."""

    _name = "mpesa.report.settlement"
    _description = "M-Pesa Settlements and Charges per Till"
    _auto = False
    _order = "month desc, journal_id"

    month = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    journal_id = fields.Many2one("account.journal", "Journal", readonly=True)
    shortcode_id = fields.Many2one("mpesa.shortcode", "Till", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    receipt_count = fields.Integer("Receipts", readonly=True)
    received = fields.Monetary(readonly=True)
    charge_count = fields.Integer("Charges (count)", readonly=True)
    charges = fields.Monetary(readonly=True)
    swept = fields.Monetary("Swept to bank", readonly=True)
    reversed_amount = fields.Monetary("Reversed", readonly=True)
    other = fields.Monetary("Other out", readonly=True)
    net = fields.Monetary("Net movement", readonly=True)
    unbooked_count = fields.Integer(
        "Lines to look at", readonly=True,
        help="Charges, sweeps, reversals and other rows not reconciled yet.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    min(l.id) AS id,
                    date_trunc('month', m.date)::date AS month,
                    l.company_id,
                    l.journal_id,
                    s.id AS shortcode_id,
                    c.currency_id,
                    count(*) FILTER (WHERE l.mpesa_line_kind = 'receipt') AS receipt_count,
                    COALESCE(sum(l.amount) FILTER (WHERE l.mpesa_line_kind = 'receipt'), 0)
                        AS received,
                    count(*) FILTER (WHERE l.mpesa_line_kind = 'charge') AS charge_count,
                    -COALESCE(sum(l.amount) FILTER (WHERE l.mpesa_line_kind = 'charge'), 0)
                        AS charges,
                    -COALESCE(sum(l.amount) FILTER (WHERE l.mpesa_line_kind = 'sweep'), 0)
                        AS swept,
                    -COALESCE(sum(l.amount) FILTER (WHERE l.mpesa_line_kind = 'reversal'), 0)
                        AS reversed_amount,
                    -COALESCE(sum(l.amount) FILTER (WHERE l.mpesa_line_kind = 'other'), 0)
                        AS other,
                    sum(l.amount) AS net,
                    count(*) FILTER (WHERE l.mpesa_line_kind != 'receipt'
                                     AND NOT l.is_reconciled) AS unbooked_count
                FROM account_bank_statement_line l
                JOIN account_move m ON m.id = l.move_id AND m.state = 'posted'
                JOIN res_company c ON c.id = l.company_id
                LEFT JOIN LATERAL (
                    SELECT min(sc.id) AS id FROM mpesa_shortcode sc
                    WHERE sc.journal_id = l.journal_id
                ) s ON TRUE
                WHERE l.mpesa_line_kind IS NOT NULL
                GROUP BY date_trunc('month', m.date), l.company_id, l.journal_id, s.id,
                         c.currency_id
            )
        """)
