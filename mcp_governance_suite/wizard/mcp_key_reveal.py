# -*- coding: utf-8 -*-
"""Show a freshly minted API key exactly once.

Only the SHA-256 digest of a key is stored, so there is no way to recover the
plaintext later - by design. This transient carries it just long enough for the
admin to copy it, then Odoo's autovacuum discards the record.
"""
from odoo import fields, models


class MCPKeyReveal(models.TransientModel):
    _name = "mcp.key.reveal"
    _description = "Reveal MCP API Key (once)"

    api_key_id = fields.Many2one("mcp.api.key", readonly=True)
    secret = fields.Char(
        string="API Key", readonly=True,
        help="Copy this now - it cannot be shown again. Only its hash is stored.")
