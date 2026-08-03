# -*- coding: utf-8 -*-
"""Bind each user to a governance scope for the OAuth (per-identity) flow.

With OAuth there is no per-connection key to hang a scope on: the user logs in
as themselves. So the effective scope is resolved here - the user's own scope
if set, otherwise the database-wide default configured in Settings.
"""
from odoo import fields, models

PARAM_DEFAULT_SCOPE = "mcp_governance_suite.default_scope_id"


class ResUsers(models.Model):
    _inherit = "res.users"

    mcp_scope_id = fields.Many2one(
        "mcp.scope",
        string="MCP Scope",
        help="Governance scope applied when this user connects an AI assistant "
             "over OAuth. Falls back to the system default when empty.")

    @property
    def SELF_READABLE_FIELDS(self):
        # Let users see (read-only) which scope governs their own AI sessions.
        return super().SELF_READABLE_FIELDS + ["mcp_scope_id"]

    def mcp_effective_scope(self):
        """Return the governance scope that should apply to this user's session."""
        self.ensure_one()
        if self.mcp_scope_id:
            return self.mcp_scope_id
        param = self.env["ir.config_parameter"].sudo().get_param(PARAM_DEFAULT_SCOPE)
        if param:
            scope = self.env["mcp.scope"].sudo().browse(int(param)).exists()
            if scope:
                return scope
        # Last resort: any active scope, preferring a read-only one.
        return self.env["mcp.scope"].sudo().search(
            [("active", "=", True)], order="read_only desc, id", limit=1)
