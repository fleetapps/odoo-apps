"""Accounting > Configuration > Settings > Accounting AI.

The API key lives in a system parameter (only Settings administrators read
those); the environment variable ANTHROPIC_API_KEY, when set on the server,
takes precedence so the key can stay out of the database altogether.
"""

from odoo import _, fields, models
from odoo.exceptions import UserError

DEFAULT_MODEL = "claude-opus-5-5"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odin_ai_api_key = fields.Char(
        string="Anthropic API key",
        config_parameter="odin_account_ai.api_key",
        groups="base.group_system")
    odin_ai_model = fields.Char(
        string="Model",
        config_parameter="odin_account_ai.model",
        default=DEFAULT_MODEL,
        help="Claude model for Ask, Explain, Anomalies and Draft Entry.")
    odin_ai_bulk_model = fields.Char(
        string="Model for Transaction Review",
        config_parameter="odin_account_ai.bulk_model",
        default=DEFAULT_MODEL,
        help="Used for the batches of suggestions, which run unattended.")
    odin_ai_retention_days = fields.Integer(
        string="Keep AI responses (days)",
        config_parameter="odin_account_ai.retention_days",
        default=90)
    odin_ai_consent = fields.Boolean(related="company_id.odin_ai_consent", readonly=False)
    odin_ai_monthly_tokens = fields.Integer(related="company_id.odin_ai_monthly_tokens", readonly=False)

    def action_odin_ai_test_connection(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only administrators test the connection."))
        message = self.env["odin.ai.llm"]._test_connection()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": message, "type": "success", "sticky": False},
        }
