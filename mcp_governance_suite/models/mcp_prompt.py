# -*- coding: utf-8 -*-
"""Prompt library - shipped MCP `prompts`, so users get value without prompting.

Instead of a manual, most competitors leave the customer staring at a blank
chat box. We ship curated, role-based prompts (Sales Manager, CFO, Warehouse
Manager, ...) that appear natively in the client's prompt picker. Admins can
add their own; partners can ship vertical prompt packs as data.
"""
import json
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MCPPrompt(models.Model):
    _name = "mcp.prompt"
    _description = "MCP Prompt"
    _order = "sequence, name"

    name = fields.Char(
        required=True, help="MCP prompt name the client references.")
    title = fields.Char(translate=True)
    persona = fields.Char(
        translate=True, help="Role this prompt is written for, e.g. 'CFO'.")
    capability_id = fields.Many2one("mcp.capability", ondelete="set null")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(translate=True, required=True)
    template = fields.Text(
        required=True, translate=True,
        help="Prompt body. {{argument}} placeholders are filled from the "
             "arguments supplied by the client at prompts/get time.")
    arguments_json = fields.Text(
        default="[]",
        help='JSON list of argument descriptors: '
             '[{"name": "period", "description": "...", "required": false}].')

    _name_uniq = models.Constraint(
        "UNIQUE (name)", "Prompt name must be unique.")

    def arguments(self):
        self.ensure_one()
        try:
            return json.loads(self.arguments_json or "[]")
        except (ValueError, TypeError):
            _logger.warning("Prompt %s has invalid arguments_json", self.name)
            return []

    def render(self, values):
        """Fill {{placeholders}} with client-supplied values (simple, safe)."""
        self.ensure_one()
        text = self.template or ""
        for key, val in (values or {}).items():
            text = text.replace("{{%s}}" % key, str(val))
        return text
