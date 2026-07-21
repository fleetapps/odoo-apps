# -*- coding: utf-8 -*-
from odoo import fields, models


class MCPScope(models.Model):
    _name = "mcp.scope"
    _description = "MCP Tool Scope"

    name = fields.Char(required=True)
    read_only = fields.Boolean(
        default=True,
        help="Global kill-switch: when on, create/write/unlink tools are not "
             "even advertised in tools/list.")
    require_approval = fields.Boolean(
        default=True,
        help="Write operations create an approval request instead of executing.")
    line_ids = fields.One2many("mcp.scope.line", "scope_id")
    rate_limit_per_hour = fields.Integer(default=500)


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
