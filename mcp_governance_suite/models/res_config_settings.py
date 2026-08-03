# -*- coding: utf-8 -*-
"""One-click setup surface (Settings -> Fleet AI).

Everything the administrator needs to turn the connector on and hand a URL to
Claude/ChatGPT/Cursor lives here. Persisted values use ir.config_parameter so
they survive upgrades and are easy to set from data or the shell.
"""
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # -- master switches ----------------------------------------------------
    mcp_enabled = fields.Boolean(
        string="Enable MCP Server",
        config_parameter="mcp_governance_suite.enabled", default=True)
    mcp_oauth_enabled = fields.Boolean(
        string="Sign in with Odoo (OAuth 2.1)",
        config_parameter="mcp_governance_suite.oauth_enabled", default=True,
        help="The recommended, browser-based connect flow. No API keys, no "
             "config files - the user just clicks Allow.")
    mcp_apikey_enabled = fields.Boolean(
        string="Allow API-key connections",
        config_parameter="mcp_governance_suite.apikey_enabled", default=True,
        help="For headless clients and CI that cannot run a browser flow.")
    mcp_dynamic_registration = fields.Boolean(
        string="Allow dynamic client registration",
        config_parameter="mcp_governance_suite.dynamic_registration", default=True,
        help="RFC 7591, deprecated by the MCP specification in favour of Client "
             "ID Metadata Documents. Kept for clients that cannot host a "
             "metadata document. Disable to lock the server to CIMD and "
             "pre-registered clients.")
    mcp_allowed_origins = fields.Char(
        string="Allowed browser origins",
        config_parameter="mcp_governance_suite.allowed_origins",
        help="Comma-separated origins permitted to call the MCP endpoint from "
             "a browser, e.g. https://app.example.com. Empty is the safe "
             "default: MCP clients are not browsers, and accepting any origin "
             "exposes the endpoint to DNS-rebinding attacks. This server's own "
             "origin is always allowed.")

    # -- defaults & lifetimes ----------------------------------------------
    mcp_default_scope_id = fields.Many2one(
        "mcp.scope", string="Default OAuth Scope",
        config_parameter="mcp_governance_suite.default_scope_id",
        help="Governance scope applied to OAuth users who have no personal "
             "scope set. Choose a read-only scope to start safe.")
    mcp_access_token_ttl = fields.Integer(
        string="Access token lifetime (seconds)",
        config_parameter="mcp_governance_suite.access_token_ttl", default=3600)
    mcp_refresh_token_ttl = fields.Integer(
        string="Refresh token lifetime (seconds)",
        config_parameter="mcp_governance_suite.refresh_token_ttl", default=2592000)
    mcp_audit_retention_months = fields.Integer(
        string="Audit retention (months)",
        config_parameter="mcp_governance_suite.retention_months", default=12)

    # -- read-only connection info (computed from the base URL) -------------
    mcp_base_url = fields.Char(compute="_compute_mcp_urls")
    mcp_endpoint_url = fields.Char(compute="_compute_mcp_urls")
    mcp_metadata_url = fields.Char(compute="_compute_mcp_urls")

    @api.depends_context("uid")
    def _compute_mcp_urls(self):
        base = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "").rstrip("/")
        for rec in self:
            rec.mcp_base_url = base
            rec.mcp_endpoint_url = f"{base}/mcp"
            rec.mcp_metadata_url = f"{base}/.well-known/oauth-protected-resource"

    def action_open_connect(self):
        """Open the Connect screen — readiness checks, URL, QR and live status."""
        return self.env["ir.actions.actions"]._for_xml_id(
            "mcp_governance_suite.mcp_connect_action")
