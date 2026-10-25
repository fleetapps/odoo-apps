# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models, tools


class MpesaReportPosSession(models.Model):
    """Report 7: each session's M-Pesa, taken against confirmed, live."""

    _name = "mpesa.report.pos.session"
    _description = "M-Pesa per POS Session"
    _auto = False
    _order = "date desc, session_id desc"

    date = fields.Date(readonly=True)
    session_id = fields.Many2one("pos.session", readonly=True)
    session_state = fields.Selection(
        [("opening_control", "Opening Control"), ("opened", "In Progress"),
         ("closing_control", "Closing Control"), ("closed", "Closed & Posted")],
        readonly=True)
    config_id = fields.Many2one("pos.config", "Point of sale", readonly=True)
    payment_method_id = fields.Many2one("pos.payment.method", readonly=True)
    shortcode_id = fields.Many2one("mpesa.shortcode", "Till", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    payment_count = fields.Integer("Payments", readonly=True)
    expected_amount = fields.Monetary("Taken", readonly=True)
    confirmed_amount = fields.Monetary("Confirmed", readonly=True)
    manual_amount = fields.Monetary("Typed, unconfirmed", readonly=True)
    difference = fields.Monetary(readonly=True)
    status = fields.Selection([("green", "Agrees"), ("red", "Differs")], readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    min(p.id) AS id,
                    (s.start_at + interval '3 hours')::date AS date,
                    s.id AS session_id,
                    s.state AS session_state,
                    s.config_id,
                    pm.id AS payment_method_id,
                    pm.mpesa_shortcode_id AS shortcode_id,
                    p.company_id,
                    c.currency_id,
                    count(p.id) AS payment_count,
                    sum(p.amount) AS expected_amount,
                    COALESCE(sum(t.amount) FILTER (WHERE t.state = 'confirmed'), 0)
                        AS confirmed_amount,
                    COALESCE(sum(t.amount) FILTER (WHERE t.state = 'manual'), 0) AS manual_amount,
                    sum(p.amount) - COALESCE(sum(t.amount) FILTER (WHERE t.state = 'confirmed'), 0)
                        AS difference,
                    CASE WHEN abs(sum(p.amount) - COALESCE(
                              sum(t.amount) FILTER (WHERE t.state = 'confirmed'), 0)) < 0.005
                         THEN 'green' ELSE 'red' END AS status
                FROM pos_payment p
                JOIN pos_payment_method pm
                  ON pm.id = p.payment_method_id AND pm.use_payment_terminal = 'mpesa'
                JOIN pos_session s ON s.id = p.session_id
                JOIN res_company c ON c.id = p.company_id
                LEFT JOIN mpesa_transaction t ON t.pos_payment_id = p.id
                GROUP BY s.id, pm.id, p.company_id, c.currency_id
            )
        """)
