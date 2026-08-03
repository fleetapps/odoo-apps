# -*- coding: utf-8 -*-
"""Immutable audit trail - one row per tool call.

This is the compliance backbone of the "enterprises can approve it" pitch:
every AI action is attributable to a real user, an auth source, a model and a
timestamp, with a rough token estimate for cost visibility. Rows are read-only
in the UI (no write access granted) and purged on a configurable retention.
"""
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class MCPAuditLog(models.Model):
    _name = "mcp.audit.log"
    _description = "MCP Audit Log"
    _order = "create_date desc"

    api_key_id = fields.Many2one("mcp.api.key", index=True, ondelete="set null")
    oauth_token_id = fields.Many2one("mcp.oauth.token", index=True, ondelete="set null")
    user_id = fields.Many2one("res.users", index=True, ondelete="set null")
    scope_id = fields.Many2one("mcp.scope", index=True, ondelete="set null")
    tool = fields.Char(index=True)
    model_name = fields.Char(string="Model", index=True)
    transport = fields.Selection(
        [("http", "Streamable HTTP"), ("apikey", "API Key"), ("oauth", "OAuth")],
        default="http")
    remote_addr = fields.Char(string="Client IP")
    args_json = fields.Text(string="Arguments")
    status = fields.Selection(
        [("ok", "OK"), ("error", "Error"), ("denied", "Denied")], index=True,
        help="'Denied' means the call was refused before it ran - the token's "
             "OAuth scope was too narrow for the tool.")
    duration_ms = fields.Integer(string="Duration (ms)")
    tokens_est = fields.Integer(
        string="Tokens (est.)",
        help="Rough size-based token estimate for cost tracking.")

    @api.model
    def cron_purge(self):
        months = int(self.env["ir.config_parameter"].sudo().get_param(
            "mcp_governance_suite.retention_months", "12"))
        if months <= 0:
            return
        cutoff = fields.Datetime.now() - relativedelta(months=months)
        self.sudo().search([("create_date", "<", cutoff)]).unlink()
