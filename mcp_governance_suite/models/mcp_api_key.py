# -*- coding: utf-8 -*-
"""Connection-scoped API keys (the non-OAuth path).

OAuth 2.1 is the recommended way to connect (see mcp_oauth.py), but keys remain
useful for headless clients, CI, and MCP clients that cannot run a browser
authorization flow. Either way the principle is identical: every call executes
*as* an Odoo user, so ir.model.access and ir.rule bound that user on top of the
MCP governance scope.

The plaintext key is shown to a human exactly once, through a reveal wizard;
only its SHA-256 digest is stored at rest.
"""
from odoo import _, api, fields, models

from .tools_crypto import hash_secret, new_secret

KEY_PREFIX = "mcp-"


class MCPApiKey(models.Model):
    _name = "mcp.api.key"
    _description = "MCP API Key"
    _order = "create_date desc"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade",
        default=lambda self: self.env.user,
        help="Every MCP call made with this key runs AS this user: "
             "ir.model.access + ir.rule apply on top of the MCP scope.")
    scope_id = fields.Many2one(
        "mcp.scope", required=True, ondelete="restrict",
        help="Governance scope: which models/operations this connection may use.")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company,
        help="Used for record-rule filtering of keys in multi-company setups.")
    key_hash = fields.Char(readonly=True, index=True, copy=False)
    key_preview = fields.Char(readonly=True, copy=False)
    is_generated = fields.Boolean(compute="_compute_is_generated")
    expiry = fields.Date(help="Optional hard expiry. After this date the key is refused.")
    last_used = fields.Datetime(readonly=True)
    call_count = fields.Integer(compute="_compute_call_count")

    _sql_constraints = [
        ("key_hash_uniq", "unique(key_hash)", "This API key already exists."),
    ]

    @api.depends("key_hash")
    def _compute_is_generated(self):
        for rec in self:
            rec.is_generated = bool(rec.key_hash)

    def _compute_call_count(self):
        data = self.env["mcp.audit.log"].sudo()._read_group(
            [("api_key_id", "in", self.ids)],
            groupby=["api_key_id"], aggregates=["__count"])
        counts = {k.id: n for k, n in data}
        for rec in self:
            rec.call_count = counts.get(rec.id, 0)

    def is_expired(self):
        self.ensure_one()
        return bool(self.expiry and fields.Date.today() > self.expiry)

    def action_generate_key(self):
        """Mint (or rotate) the secret and reveal it once via a wizard.

        Rotating invalidates the previous secret immediately - the safe default
        for credential compromise response.
        """
        self.ensure_one()
        raw = new_secret(prefix=KEY_PREFIX, nbytes=32)
        self.write({"key_hash": hash_secret(raw), "key_preview": raw[:12] + "..."})
        wizard = self.env["mcp.key.reveal"].create({
            "api_key_id": self.id, "secret": raw})
        return {
            "type": "ir.actions.act_window",
            "name": _("Copy your MCP API key"),
            "res_model": "mcp.key.reveal",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def mcp_authenticate(self, presented_key):
        """Resolve a presented bearer secret to a live key record, or empty."""
        key = self.sudo().search(
            [("key_hash", "=", hash_secret(presented_key)),
             ("active", "=", True)], limit=1)
        if not key or key.is_expired():
            return self.browse()
        return key
