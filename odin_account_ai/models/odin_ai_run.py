"""One line per call to the model: who, for what, how many tokens, how long,
and how it ended. The monthly budget is counted from here, and a stored
response is reused when Odoo retries a request (a serialization failure
replays the whole request; the API must not be paid twice)."""

from datetime import timedelta

from odoo import api, fields, models


class OdinAiRun(models.Model):
    _name = "odin.ai.run"
    _description = "AI call"
    _order = "id desc"

    step_key = fields.Char(index=True, readonly=True)
    user_id = fields.Many2one("res.users", readonly=True, index=True)
    company_id = fields.Many2one("res.company", readonly=True, index=True)
    feature = fields.Selection([
        ("ask", "Ask"),
        ("explain", "Explain"),
        ("review", "Transaction Review"),
        ("anomaly", "Anomalies"),
        ("draft", "Draft Entry"),
        ("test", "Test"),
    ], readonly=True)
    model = fields.Char(readonly=True)
    served_model = fields.Char(readonly=True, help="The model that answered, after any fallback.")
    effort = fields.Char(readonly=True)
    status = fields.Selection([
        ("ok", "Answered"),
        ("refusal", "Declined"),
        ("max_tokens", "Cut short"),
        ("context", "Too long"),
        ("error", "Error"),
    ], readonly=True)
    request_id = fields.Char(readonly=True)
    input_tokens = fields.Integer(readonly=True)
    output_tokens = fields.Integer(readonly=True)
    cache_read_tokens = fields.Integer(readonly=True)
    cache_write_tokens = fields.Integer(readonly=True)
    total_tokens = fields.Integer(compute="_compute_total", store=True)
    budget_tokens = fields.Integer(
        compute="_compute_total", store=True,
        help="What the call counts against the monthly budget: cached input at its price "
             "(reads about a tenth of normal input, 5-minute writes a quarter more).")
    duration_ms = fields.Integer(readonly=True)
    error = fields.Text(readonly=True)
    response = fields.Text(readonly=True, help="The response, kept for the retention period.")

    @api.depends("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    def _compute_total(self):
        # Cache reads cost at most 0.1x the base input price (less on the
        # newest models) and 5-minute cache writes 1.25x ("Economics", prompt
        # caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
        # Counting a cached conversation at full weight would spend the budget
        # many times faster than the bill.
        for run in self:
            run.total_tokens = (
                run.input_tokens + run.output_tokens + run.cache_read_tokens + run.cache_write_tokens)
            run.budget_tokens = round(
                run.input_tokens + run.output_tokens + run.cache_write_tokens * 1.25
                + run.cache_read_tokens * 0.1)

    @api.model
    def _cron_purge(self):
        """Erase what the AI answered once the retention period is over: the
        stored responses here (the log line stays), and the Ask conversations
        nobody has touched since, which hold the same answers and the ledger
        figures the tools read for them."""
        days = int(self.env["ir.config_parameter"].sudo().get_param("odin_account_ai.retention_days", 90))
        cutoff = fields.Datetime.now() - timedelta(days=max(days, 1))
        self.sudo().search([("create_date", "<", cutoff), ("response", "!=", False)]).write({"response": False})
        self.env["odin.ai.conversation"].sudo().search([("write_date", "<", cutoff)]).unlink()
