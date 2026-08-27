# -*- coding: utf-8 -*-
"""Capability & Tool registry - the data-driven surface the AI discovers.

Rather than hard-coding a handful of tools in Python, tools are *records*. A
capability (Sales, Inventory, CRM, ...) bundles a set of tools; a governance
scope switches whole capabilities on or off. Downstream modules extend the
connector simply by shipping new mcp.capability / mcp.tool data plus, if needed,
a new handler method on mcp.engine - no controller surgery, upgrade-safe.

Each tool row names a generic *handler* implemented once on the engine. The
handler is data-selected, so partners compose new tools out of existing verbs
(search, read_group, name_search, ...) without writing code.
"""
from odoo import api, fields, models

# The verbs the engine implements. A tool row binds a name + JSON schema to one
# of these. `writes=True` tools are hidden from read-only scopes.
HANDLER_SELECTION = [
    ("list_capabilities", "List capabilities & tools"),
    ("list_models", "List accessible models"),
    ("get_schema", "Describe a model's fields (knowledge)"),
    ("get_business_context", "Explain a model in business terms"),
    ("search_records", "Search records (domain)"),
    ("count_records", "Count records (domain)"),
    ("name_search", "Resolve names to ids"),
    ("read_group", "Aggregate / group-by (reports & dashboards)"),
    ("create_record", "Create a record"),
    ("write_record", "Update a record"),
    ("unlink_record", "Delete a record"),
    ("call_method", "Call an allow-listed business method"),
]
# Mutating verbs: hidden from read-only scopes and from tokens without
# odoo:write. call_method belongs here because an allow-listed business method
# (confirm, post, send) almost always changes state.
#
# Read this through mcp.tool._write_handlers(), never directly: a module that
# adds a writing verb has to be able to add it to this set, and the failure
# mode if it cannot is that a read-only scope advertises and permits a write
# tool. See _compute_writes.
WRITE_HANDLERS = {"create_record", "write_record", "unlink_record", "call_method"}


class MCPCapability(models.Model):
    _name = "mcp.capability"
    _description = "MCP Capability"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    technical_name = fields.Char(
        required=True, help="Stable identifier used by scopes and clients.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(
        translate=True,
        help="Business-facing summary. Exposed to the AI so it understands what "
             "this capability is for.")
    tool_ids = fields.One2many("mcp.tool", "capability_id")
    tool_count = fields.Integer(compute="_compute_tool_count")

    _technical_name_uniq = models.Constraint(
        "UNIQUE (technical_name)", "Capability technical name must be unique.")

    def _compute_tool_count(self):
        for rec in self:
            rec.tool_count = len(rec.tool_ids)


class MCPTool(models.Model):
    _name = "mcp.tool"
    _description = "MCP Tool"
    _order = "capability_id, sequence, name"

    name = fields.Char(
        required=True,
        help="The MCP tool name the client calls, e.g. search_records.")
    title = fields.Char(translate=True, help="Human title shown by MCP clients.")
    capability_id = fields.Many2one(
        "mcp.capability", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(
        required=True, translate=True,
        help="Exposed verbatim to the AI: describe when and how to use the tool.")
    handler = fields.Selection(
        HANDLER_SELECTION, required=True,
        help="Which engine verb backs this tool.")
    writes = fields.Boolean(
        compute="_compute_writes", store=True,
        help="Mutating tools are hidden from read-only scopes automatically.")
    input_schema = fields.Text(
        default="{}",
        help="JSON Schema advertised as the tool's inputSchema.")

    _name_uniq = models.Constraint(
        "UNIQUE (name)", "Tool name must be unique.")

    @api.model
    def _write_handlers(self):
        """Which engine verbs change data. The extension point for new tools.

        A module adding a handler that writes MUST add it here by overriding
        this method, or its tool is advertised to read-only scopes and executes
        for connections that were never granted ``odoo:write``. That is the one
        way a downstream module can silently punch through the governance
        layer, so ``tests/test_permissions.py`` fails if any registered handler
        is classified neither here nor in the read set.
        """
        return set(WRITE_HANDLERS)

    @api.depends("handler")
    def _compute_writes(self):
        writers = self._write_handlers()
        for rec in self:
            rec.writes = rec.handler in writers
