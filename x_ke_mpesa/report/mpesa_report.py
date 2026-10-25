# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""SQL views behind the M-Pesa reports.

Plain models with stored-looking fields, so pivots, graphs and ai_dashboards
read them through ``_read_group`` like any other model, under the same
multi-company rules.
"""

from odoo import fields, models, tools

AGE_BUCKETS = [
    ("0", "Today"),
    ("1", "1-2 days"),
    ("3", "3-7 days"),
    ("8", "8-30 days"),
    ("31", "Over 30 days"),
]


class MpesaReportTillDay(models.Model):
    """Report 2: each till, each day, red or green."""

    _name = "mpesa.report.till.day"
    _description = "M-Pesa Till Reconciliation per Day"
    _auto = False
    _order = "date desc, shortcode_id"

    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    shortcode_id = fields.Many2one("mpesa.shortcode", "Till", readonly=True)
    analytic_account_id = fields.Many2one("account.analytic.account", "Outlet", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    receipt_count = fields.Integer("Receipts", readonly=True)
    confirmed_amount = fields.Monetary("Confirmed", readonly=True)
    allocated_amount = fields.Monetary("Allocated", readonly=True)
    claimed_amount = fields.Monetary("Claimed, settles at close", readonly=True)
    unallocated_amount = fields.Monetary("Unallocated", readonly=True)
    parked_amount = fields.Monetary("Parked", readonly=True)
    manual_unconfirmed_amount = fields.Monetary("Manual, unconfirmed", readonly=True)
    exception_count = fields.Integer("Exceptions", readonly=True)
    status = fields.Selection([("green", "Reconciled"), ("red", "Needs attention")],
                              readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    min(t.id) AS id,
                    t.trans_date AS date,
                    t.company_id,
                    t.shortcode_id,
                    t.analytic_account_id,
                    c.currency_id,
                    count(*) FILTER (WHERE t.state = 'confirmed') AS receipt_count,
                    COALESCE(sum(t.amount) FILTER (WHERE t.state = 'confirmed'), 0)
                        AS confirmed_amount,
                    COALESCE(sum(t.amount) FILTER (WHERE t.allocation_state = 'allocated'), 0)
                        AS allocated_amount,
                    COALESCE(sum(t.amount) FILTER (WHERE t.allocation_state = 'claimed'), 0)
                        AS claimed_amount,
                    COALESCE(sum(t.amount) FILTER (
                        WHERE t.allocation_state IN ('unallocated', 'pending')
                          AND t.state = 'confirmed'), 0) AS unallocated_amount,
                    COALESCE(sum(t.amount) FILTER (WHERE t.allocation_state = 'parked'), 0)
                        AS parked_amount,
                    COALESCE(sum(t.amount) FILTER (WHERE t.state = 'manual'), 0)
                        AS manual_unconfirmed_amount,
                    count(*) FILTER (WHERE t.exception != 'none') AS exception_count,
                    CASE WHEN count(*) FILTER (
                            WHERE t.state = 'manual'
                               OR t.exception != 'none'
                               OR (t.state = 'confirmed'
                                   AND t.allocation_state IN ('unallocated', 'pending'))
                         ) = 0
                         THEN 'green' ELSE 'red' END AS status
                FROM mpesa_transaction t
                JOIN res_company c ON c.id = t.company_id
                WHERE t.state IN ('confirmed', 'manual') AND t.trans_date IS NOT NULL
                GROUP BY t.trans_date, t.company_id, t.shortcode_id,
                         t.analytic_account_id, c.currency_id
            )
        """)


class MpesaReportUnallocated(models.Model):
    """Report 3: unallocated receipts and how old they are."""

    _name = "mpesa.report.unallocated"
    _description = "Unallocated M-Pesa Receipts"
    _auto = False
    _order = "trans_date, id"

    transaction_id = fields.Many2one("mpesa.transaction", "Transaction", readonly=True)
    receipt = fields.Char(readonly=True)
    trans_date = fields.Date("Date", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    shortcode_id = fields.Many2one("mpesa.shortcode", "Till", readonly=True)
    analytic_account_id = fields.Many2one("account.analytic.account", "Outlet", readonly=True)
    channel = fields.Selection(
        [("stk", "STK Push"), ("till", "Paid to till"), ("paybill", "Paybill"),
         ("manual", "Manual")], readonly=True)
    allocation_state = fields.Selection(
        [("unallocated", "Unallocated"), ("parked", "Parked in suspense"),
         ("pending", "Not in the bank yet")], readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    amount = fields.Monetary(readonly=True)
    age_days = fields.Integer("Age (days)", readonly=True, aggregator="max")
    age_bucket = fields.Selection(AGE_BUCKETS, "Age", readonly=True)
    customer_name = fields.Char(readonly=True)
    bill_ref = fields.Char("Account number", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    t.id,
                    t.id AS transaction_id,
                    t.receipt,
                    t.trans_date,
                    t.company_id,
                    t.shortcode_id,
                    t.analytic_account_id,
                    t.channel,
                    t.allocation_state,
                    c.currency_id,
                    t.amount,
                    (CURRENT_DATE - t.trans_date) AS age_days,
                    CASE
                        WHEN CURRENT_DATE - t.trans_date < 1 THEN '0'
                        WHEN CURRENT_DATE - t.trans_date < 3 THEN '1'
                        WHEN CURRENT_DATE - t.trans_date < 8 THEN '3'
                        WHEN CURRENT_DATE - t.trans_date < 31 THEN '8'
                        ELSE '31'
                    END AS age_bucket,
                    t.customer_name,
                    t.bill_ref
                FROM mpesa_transaction t
                JOIN res_company c ON c.id = t.company_id
                WHERE t.state = 'confirmed'
                  AND t.allocation_state IN ('unallocated', 'parked', 'pending')
            )
        """)


class MpesaReportCallbackHealth(models.Model):
    """Report 6: hours in trading time with no callback at all, per shortcode."""

    _name = "mpesa.report.callback.health"
    _description = "M-Pesa Callback Health"
    _auto = False
    _order = "hour desc, shortcode_id"

    hour = fields.Datetime(readonly=True)
    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    shortcode_id = fields.Many2one("mpesa.shortcode", readonly=True)
    callback_count = fields.Integer("Callbacks", readonly=True)
    failed_count = fields.Integer("Failed", readonly=True)
    in_trading_hours = fields.Boolean(readonly=True)
    gap = fields.Boolean("Silent trading hour", readonly=True,
                         help="A trading hour in which this shortcode received no "
                              "callback at all: worth checking the Org Portal.")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        # Nairobi is UTC+3 all year: hours are generated in UTC and compared
        # with trading hours in local time.
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                WITH hours AS (
                    SELECT s.id AS shortcode_id, s.company_id, s.trading_hour_from,
                           s.trading_hour_to, h AS hour
                      FROM mpesa_shortcode s
                     CROSS JOIN generate_series(
                           date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '13 days',
                           date_trunc('hour', now() AT TIME ZONE 'UTC'),
                           interval '1 hour') AS h
                     WHERE s.active AND s.state = 'live'
                )
                SELECT
                    row_number() OVER (ORDER BY hours.hour, hours.shortcode_id) AS id,
                    hours.hour,
                    (hours.hour + interval '3 hours')::date AS date,
                    hours.company_id,
                    hours.shortcode_id,
                    count(cb.id) AS callback_count,
                    count(cb.id) FILTER (WHERE cb.state = 'error') AS failed_count,
                    (extract(hour FROM hours.hour + interval '3 hours')
                        BETWEEN hours.trading_hour_from AND hours.trading_hour_to - 1)
                        AS in_trading_hours,
                    (count(cb.id) = 0 AND extract(hour FROM hours.hour + interval '3 hours')
                        BETWEEN hours.trading_hour_from AND hours.trading_hour_to - 1)
                        AS gap
                FROM hours
                LEFT JOIN mpesa_callback cb
                       ON cb.shortcode_id = hours.shortcode_id
                      AND cb.received_at >= hours.hour
                      AND cb.received_at < hours.hour + interval '1 hour'
                GROUP BY hours.hour, hours.shortcode_id, hours.company_id,
                         hours.trading_hour_from, hours.trading_hour_to
            )
        """)


class MpesaReportStk(models.Model):
    """Report 5: STK Push by outlet and cashier, outcome by outcome."""

    _name = "mpesa.report.stk"
    _description = "M-Pesa STK Performance"
    _auto = False
    _order = "date desc"

    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    shortcode_id = fields.Many2one("mpesa.shortcode", "Till", readonly=True)
    analytic_account_id = fields.Many2one("account.analytic.account", "Outlet", readonly=True)
    user_id = fields.Many2one("res.users", "Cashier", readonly=True)
    request_count = fields.Integer("Requests", readonly=True)
    success_count = fields.Integer("Paid", readonly=True)
    cancelled_count = fields.Integer("Cancelled", readonly=True)
    timeout_count = fields.Integer("No answer", readonly=True,
                                   help="Phone unreachable, prompt expired, or outcome unknown.")
    insufficient_count = fields.Integer("Insufficient funds", readonly=True)
    wrong_pin_count = fields.Integer("Wrong PIN", readonly=True)
    other_failure_count = fields.Integer("Other failures", readonly=True)
    seconds_to_confirm = fields.Float("Seconds to confirm", readonly=True, aggregator="avg")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT
                    min(t.id) AS id,
                    (COALESCE(t.requested_at, t.create_date) + interval '3 hours')::date AS date,
                    t.company_id,
                    t.shortcode_id,
                    t.analytic_account_id,
                    t.user_id,
                    count(*) AS request_count,
                    count(*) FILTER (WHERE t.state = 'confirmed') AS success_count,
                    count(*) FILTER (WHERE t.result_code = '1032') AS cancelled_count,
                    count(*) FILTER (WHERE t.result_code IN ('1037', '1019')
                                        OR t.state = 'unknown') AS timeout_count,
                    count(*) FILTER (WHERE t.result_code = '1') AS insufficient_count,
                    count(*) FILTER (WHERE t.result_code = '2001') AS wrong_pin_count,
                    count(*) FILTER (WHERE t.state = 'failed' AND COALESCE(t.result_code, '')
                                     NOT IN ('1032', '1037', '1019', '1', '2001'))
                        AS other_failure_count,
                    COALESCE(avg(t.seconds_to_confirm) FILTER (
                        WHERE t.state = 'confirmed' AND t.seconds_to_confirm > 0), 0)
                        AS seconds_to_confirm
                FROM mpesa_transaction t
                WHERE t.source = 'stk' AND t.state != 'draft'
                GROUP BY 2, t.company_id, t.shortcode_id, t.analytic_account_id, t.user_id
            )
        """)
