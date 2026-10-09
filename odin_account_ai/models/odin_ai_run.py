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
        ("error", "Error"),
    ], readonly=True)
    request_id = fields.Char(readonly=True)
    input_tokens = fields.Integer(readonly=True)
    output_tokens = fields.Integer(readonly=True)
    cache_read_tokens = fields.Integer(readonly=True)
    cache_write_tokens = fields.Integer(readonly=True)
    total_tokens = fields.Integer(compute="_compute_total", store=True)
    duration_ms = fields.Integer(readonly=True)
    error = fields.Text(readonly=True)
    response = fields.Text(readonly=True, help="The response, kept for the retention period.")

    @api.depends("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    def _compute_total(self):
        for run in self:
            run.total_tokens = (
                run.input_tokens + run.output_tokens + run.cache_read_tokens + run.cache_write_tokens)

    @api.model
    def _cron_purge(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param("odin_account_ai.retention_days", 90))
        cutoff = fields.Datetime.now() - timedelta(days=max(days, 1))
        self.sudo().search([("create_date", "<", cutoff), ("response", "!=", False)]).write({"response": False})
