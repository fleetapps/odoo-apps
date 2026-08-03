# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MCPScope(models.Model):
    _name = "mcp.scope"
    _description = "MCP Tool Scope"
    _order = "name"

    name = fields.Char(required=True)
    read_only = fields.Boolean(
        default=True,
        help="Global kill-switch: when on, create/write/unlink tools are not "
             "even advertised in tools/list.")
    require_approval = fields.Boolean(
        default=True,
        help="Write operations create an approval request instead of executing.")
    line_ids = fields.One2many("mcp.scope.line", "scope_id")
    rate_limit_per_hour = fields.Integer(
        default=500, help="Calls per hour for keys bound to this scope. "
                          "0 falls back to the instance default in Settings.")

    # Reverse of mcp.api.key.scope_id, so the list and the stat button can say
    # how much is riding on a scope before someone widens it.
    key_ids = fields.One2many("mcp.api.key", "scope_id", string="API Keys")
    key_count = fields.Integer(compute="_compute_counts", store=True)
    model_count = fields.Integer(
        string="Allowed Models", compute="_compute_counts", store=True)

    @api.depends("line_ids", "key_ids")
    def _compute_counts(self):
        for scope in self:
            scope.model_count = len(scope.line_ids)
            scope.key_count = len(scope.key_ids)

    def action_open_keys(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("API Keys"),
            "res_model": "mcp.api.key",
            "view_mode": "list,form",
            "domain": [("scope_id", "=", self.id)],
            "context": {"default_scope_id": self.id},
        }


class MCPScopeLine(models.Model):
    _name = "mcp.scope.line"
    _description = "MCP Scope Line (per model)"

    scope_id = fields.Many2one("mcp.scope", required=True, ondelete="cascade")
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(related="model_id.model", store=True)
    can_read = fields.Boolean(default=True)
    can_create = fields.Boolean(default=False)
    can_write = fields.Boolean(default=False)
    can_unlink = fields.Boolean(default=False)
    field_blacklist = fields.Char(
        help="Comma-separated field names never returned/accepted, "
             "e.g. password,vat,bank_ids")
    record_domain = fields.Char(
        default="[]",
        help="Extra domain ANDed to every read/search on this model.")
