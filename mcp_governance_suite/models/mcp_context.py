# -*- coding: utf-8 -*-
"""Business Context Engine - teach the AI what your data *means*.

A raw MCP connector shows the AI `sale.order.state` and `invoice_status`. This
model lets an admin attach plain-language meaning ("A confirmed quotation
becomes a sales order and reserves stock") to a model or capability. That
context is surfaced through the get_business_context tool and as MCP resources,
so the AI reasons about your business, not just your columns.
"""
from odoo import fields, models


class MCPContext(models.Model):
    _name = "mcp.context"
    _description = "MCP Business Context"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    capability_id = fields.Many2one("mcp.capability", ondelete="cascade")
    model_id = fields.Many2one(
        "ir.model", ondelete="cascade",
        help="Attach this context to a specific model. Leave empty for general "
             "company/domain knowledge.")
    model_name = fields.Char(related="model_id.model", store=True, index=True)
    body = fields.Text(
        required=True, translate=True,
        help="Plain-language explanation exposed to the AI assistant.")

    def as_payload(self):
        """Serialise for the get_business_context tool / MCP resources."""
        return [{
            "title": rec.name,
            "model": rec.model_name or None,
            "capability": rec.capability_id.technical_name or None,
            "context": rec.body,
        } for rec in self]
