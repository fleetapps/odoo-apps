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
from odoo.exceptions import ValidationError

from .tools_crypto import hash_secret, new_secret

KEY_PREFIX = "mcp-"
GROUP_ADMIN = "mcp_governance_suite.group_mcp_admin"


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
        default=lambda self: self.env.user.sudo().mcp_effective_scope(),
        help="Governance scope: which models/operations this connection may use.")
    can_choose_scope = fields.Boolean(
        compute="_compute_can_choose_scope",
        help="Only an Odoo MCP administrator picks a scope other than the one "
             "the key's user is already governed by.")
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company,
        help="Used for record-rule filtering of keys in multi-company setups.")
    key_hash = fields.Char(readonly=True, index=True, copy=False)
    key_preview = fields.Char(readonly=True, copy=False)
    is_generated = fields.Boolean(compute="_compute_is_generated")
    expiry = fields.Date(help="Optional hard expiry. After this date the key is refused.")
    last_used = fields.Datetime(readonly=True)
    call_count = fields.Integer(compute="_compute_call_count")

    # Odoo 19 dropped _sql_constraints: it only logs "no longer supported" and
    # the constraint never reaches the database, so this uniqueness was not
    # actually being enforced. models.Constraint is the replacement.
    _key_hash_uniq = models.Constraint(
        "UNIQUE (key_hash)", "This API key already exists.")

    @api.depends_context("uid")
    def _compute_can_choose_scope(self):
        allowed = self.env.su or self.env.user.has_group(GROUP_ADMIN)
        for rec in self:
            rec.can_choose_scope = allowed

    @api.constrains("scope_id", "user_id")
    def _check_scope_is_the_users_own(self):
        """A non-administrator may only key the scope they are already governed by.

        Every employee holds the MCP User role, so anyone can mint a key for
        themselves - which is the point, it is how a headless client connects.
        But the scope is chosen on this form, and mcp.scope is readable by that
        same role, so without this an employee could bind their key to whatever
        permissive scope happens to exist in the database and step around the
        approval gate an administrator configured for them.

        It is not a privilege escalation either way: the key still runs as its
        user and can never exceed that user's own Odoo rights. It is the
        governance controls - read-only, and require-approval - that this
        protects, and those are the whole reason the module exists.
        """
        if self.env.su or self.env.user.has_group(GROUP_ADMIN):
            return
        for rec in self:
            effective = rec.user_id.sudo().mcp_effective_scope()
            if rec.scope_id != effective:
                raise ValidationError(_(
                    "This key has to use the scope you are already governed "
                    "by%(named)s. Ask an Odoo MCP administrator if you need a "
                    "different one.",
                    named=(" (\"%s\")" % effective.name) if effective else ""))

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
