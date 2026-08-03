# -*- coding: utf-8 -*-
"""Settings > MCP Governance.

Spec §4.1. Everything here is stored as an `ir.config_parameter` via the
`config_parameter` attribute, which is the documented way to persist a
res.config.settings field without adding a column:
https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html

Every field below is read somewhere in `controllers/mcp.py`,
`models/mcp_engine.py` or `models/mcp_audit.py` - no decorative switches.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

PARAM = "mcp_governance_suite.%s"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # --- Access --------------------------------------------------------------
    mcp_enabled = fields.Boolean(
        string="Enable MCP Server",
        config_parameter=PARAM % "enabled",
        help="Master switch. When off, /mcp/v1 answers 503 to every caller, "
             "including keys that are otherwise valid. Off by default so the "
             "endpoint is not live the moment the module is installed.")
    mcp_api_key_enabled = fields.Boolean(
        string="Allow API Key Authentication",
        default=True,
        config_parameter=PARAM % "api_key_enabled",
        help="Bearer-token auth against the API Keys list. Turn off once an "
             "OAuth 2.1 flow is in place and no local client needs a static key.")

    # --- Logging -------------------------------------------------------------
    mcp_log_requests = fields.Boolean(
        string="Log Every Request",
        default=True,
        config_parameter=PARAM % "log_requests",
        help="Write an audit row for every tool call. Turning this off blinds "
             "the Audit Log - the whole point of the governance layer - so "
             "leave it on unless volume is genuinely a problem.")
    mcp_log_retention_days = fields.Integer(
        string="Keep Logs For (days)",
        default=365,
        config_parameter=PARAM % "log_retention_days",
        help="The weekly purge cron deletes audit rows older than this. "
             "0 keeps them forever.")

    # --- Limits --------------------------------------------------------------
    mcp_default_record_limit = fields.Integer(
        string="Default Record Limit",
        default=10,
        config_parameter=PARAM % "default_record_limit",
        help="How many records a search returns when the caller names no "
             "limit. Small on purpose: unbounded reads blow the model's "
             "context window and cost real money.")
    mcp_max_record_limit = fields.Integer(
        string="Maximum Record Limit",
        default=100,
        config_parameter=PARAM % "max_record_limit",
        help="Hard ceiling. A caller asking for more is silently clamped to "
             "this, never refused.")
    mcp_rate_limit_per_hour = fields.Integer(
        string="Default Rate Limit (calls/hour)",
        default=500,
        config_parameter=PARAM % "rate_limit_per_hour",
        help="Applied to keys whose scope leaves its own rate limit at 0. "
             "0 here too means unlimited.")

    # ------------------------------------------------------------------ guards
    @api.constrains("mcp_default_record_limit", "mcp_max_record_limit",
                    "mcp_log_retention_days", "mcp_rate_limit_per_hour")
    def _check_limits(self):
        for rec in self:
            if rec.mcp_default_record_limit < 1:
                raise ValidationError(
                    self.env._("Default Record Limit must be at least 1."))
            if rec.mcp_max_record_limit < rec.mcp_default_record_limit:
                raise ValidationError(self.env._(
                    "Maximum Record Limit cannot be below the default "
                    "(%(default)s).", default=rec.mcp_default_record_limit))
            if rec.mcp_log_retention_days < 0 or rec.mcp_rate_limit_per_hour < 0:
                raise ValidationError(
                    self.env._("Retention and rate limit cannot be negative."))

    # ------------------------------------------------------------- shortcuts
    # The Settings page is where an admin lands first; these buttons take them
    # to the records that actually have to exist, in the order they matter.
    def action_open_mcp_scopes(self):
        return self.env["ir.actions.act_window"]._for_xml_id(
            "mcp_governance_suite.mcp_scope_action")

    def action_open_mcp_keys(self):
        return self.env["ir.actions.act_window"]._for_xml_id(
            "mcp_governance_suite.mcp_key_action")

    def action_open_mcp_audit(self):
        return self.env["ir.actions.act_window"]._for_xml_id(
            "mcp_governance_suite.mcp_audit_action")


class MCPConfig(models.AbstractModel):
    """Read-side helper so the controller/engine never re-parse params by hand."""
    _name = "mcp.config"
    _description = "MCP Runtime Configuration"

    @api.model
    def get(self, name, default=None, cast=int):
        raw = self.env["ir.config_parameter"].sudo().get_param(PARAM % name)
        if raw in (None, "", False):
            return default
        if cast is bool:
            return raw not in ("False", "false", "0")
        try:
            return cast(raw)
        except (TypeError, ValueError):
            return default
