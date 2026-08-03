# -*- coding: utf-8 -*-
import hashlib
import secrets
from odoo import api, fields, models


class MCPApiKey(models.Model):
    _name = "mcp.api.key"
    _description = "MCP API Key"
    _order = "name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    user_id = fields.Many2one("res.users", required=True,
                              help="All MCP calls with this key run AS this user: "
                                   "ir.model.access + ir.rule apply on top of scopes.")
    scope_id = fields.Many2one("mcp.scope", required=True)
    key_hash = fields.Char(readonly=True, index=True)
    key_preview = fields.Char(readonly=True)
    expiry = fields.Date()
    last_used = fields.Datetime(readonly=True)

    # Not stored: it turns on when a date passes, and nothing writes the record
    # on that day, so a stored value would simply be wrong. The explicit
    # ``search`` is what makes it usable in the search view's filter domains -
    # a compute with neither ``store`` nor ``search`` raises "Unsearchable
    # field" at install time, not at first use.
    expired = fields.Boolean(
        string="Expired", compute="_compute_expired", search="_search_expired",
        help="The expiry date has passed. The key is refused at authentication.")

    @api.depends("expiry")
    def _compute_expired(self):
        today = fields.Date.today()
        for key in self:
            key.expired = bool(key.expiry and key.expiry < today)

    def _search_expired(self, operator, value):
        if operator not in ("=", "!="):
            raise NotImplementedError
        today = fields.Date.today()
        is_expired = bool(value) if operator == "=" else not value
        if is_expired:
            return [("expiry", "!=", False), ("expiry", "<", today)]
        return ["|", ("expiry", "=", False), ("expiry", ">=", today)]

    def is_expired(self):
        self.ensure_one()
        return bool(self.expiry and fields.Date.today() > self.expiry)

    def action_generate_key(self):
        """Mint the secret and show it once, in a sticky notification.

        Generation is an explicit button rather than a side effect of create()
        because the raw secret is never persisted - only its sha256 - so the
        one moment it exists has to be a moment the admin is looking at. A
        second press invalidates the previous secret.
        """
        self.ensure_one()
        raw = "mcp_" + secrets.token_urlsafe(32)
        self.write({
            "key_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "key_preview": raw[:12] + "…",
        })
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning",
                "title": self.env._("Copy this key now"),
                "message": self.env._(
                    "%(key)s\n\nOnly its hash is stored. Closing this notice "
                    "is the last time this value exists anywhere.", key=raw),
                "sticky": True,
            },
        }

    def is_usable(self):
        self.ensure_one()
        return bool(self.key_hash) and self.active and not self.is_expired()
