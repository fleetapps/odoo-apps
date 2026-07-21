# -*- coding: utf-8 -*-
import hashlib
import secrets
from odoo import api, fields, models


class MCPApiKey(models.Model):
    _name = "mcp.api.key"
    _description = "MCP API Key"

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

    def is_expired(self):
        self.ensure_one()
        return bool(self.expiry and fields.Date.today() > self.expiry)

    @api.model_create_multi
    def create(self, vals_list):
        # api.model_create_multi is the documented batch-create decorator (ORM ref)
        records = super().create(vals_list)
        for rec in records:
            raw = "mcp_" + secrets.token_urlsafe(32)
            rec.key_hash = hashlib.sha256(raw.encode()).hexdigest()
            rec.key_preview = raw[:8] + "..."
            # TODO(build): surface `raw` ONCE to the admin (dialog / sticky
            # notification). Only the sha256 hash is stored at rest.
        return records
