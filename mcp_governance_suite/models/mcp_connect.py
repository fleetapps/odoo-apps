# -*- coding: utf-8 -*-
"""Backing data for the "Connect your AI" screen.

The whole screen is driven by one call, ``get_state()``, so the client stays a
thin renderer and everything interesting stays testable in Python.

The design goal is that a user never learns something failed by switching to
Claude and watching it not work. Every precondition that can be checked from
inside Odoo is checked here, stated in plain language, and paired with a button
that opens the exact screen where it is fixed.
"""
import base64
import io
import logging

from odoo import _, api, fields, models
from odoo.tools import config

try:  # bundled with Odoo, but never let a missing extra break the page
    import qrcode
except ImportError:  # pragma: no cover
    qrcode = None

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

_logger = logging.getLogger(__name__)

PARAM_REACH_STATE = "mcp_governance_suite.reachability_state"
PARAM_REACH_AT = "mcp_governance_suite.reachability_checked_at"
REACH_TIMEOUT = 4

# Per-client instructions. Kept as data rather than prose in a template so the
# picker can swap them without a page reload.
CLIENT_STEPS = {
    "claude": {
        "name": "Claude",
        "steps": [
            "Open Claude → Settings → Connectors.",
            "Click <b>Add custom connector</b>.",
            "Paste the server URL and click <b>Add</b>.",
            "Click <b>Connect</b>, then <b>Allow</b> in the browser window.",
        ],
        "note": "Leave Client ID and Client Secret empty — this server "
                "registers your client automatically.",
    },
    "chatgpt": {
        "name": "ChatGPT",
        "steps": [
            "Enable <b>Developer Mode</b> in ChatGPT settings (paid plan required).",
            "Go to Settings → Connectors → <b>Create</b>.",
            "Paste the server URL and choose OAuth authentication.",
            "Approve the connection in the browser window.",
        ],
        "note": "ChatGPT only supports remote HTTPS servers — your Odoo must be "
                "reachable from the internet.",
    },
    "vscode": {
        "name": "VS Code",
        "steps": [
            "Open the Command Palette and run <b>MCP: Add Server</b>.",
            "Choose <b>HTTP</b> and paste the server URL.",
            "Sign in when the browser window opens.",
        ],
        "config": True,
    },
    "cursor": {
        "name": "Cursor",
        "steps": [
            "Open Cursor → Settings → <b>MCP</b>.",
            "Click <b>Add new global MCP server</b>.",
            "Paste the configuration below, then sign in when prompted.",
        ],
        "config": True,
    },
    "other": {
        "name": "Any MCP client",
        "steps": [
            "Add a remote / HTTP MCP server in your client.",
            "Paste the server URL below.",
            "Your client discovers sign-in automatically and opens a browser.",
        ],
        "note": "This server implements the open Model Context Protocol, so any "
                "compliant client works. See modelcontextprotocol.io/clients.",
    },
}

# Shown as one-click chips. `writes` entries are hidden when no scope permits
# writing, so the screen never advertises something that will be refused.
STARTER_PROMPTS = [
    (False, "Show me all customers from France"),
    (False, "Find products with stock below 10 units"),
    (False, "List today's sales orders over $1900"),
    (False, "Search for unpaid invoices from last month"),
    (False, "Break down revenue by month for this year"),
    (True, "Create a draft sale order for Deco Addict"),
    (True, "Create partners from the attached document"),
]


class MCPConnect(models.TransientModel):
    _name = "mcp.connect"
    _description = "Connect your AI"

    # ------------------------------------------------------------------ state
    @api.model
    def get_state(self):
        """Everything the Connect screen renders, in one round trip.

        Deliberately does no network I/O: this is polled, and a hanging outbound
        request would freeze the page. Reachability lives in test_reachability().
        """
        base = self._base_url()
        checks = self._readiness_checks(base)
        return {
            "can_admin": self.env.user.has_group("mcp_governance_suite.group_mcp_admin"),
            "checks": checks,
            "ready": all(c["state"] != "fail" for c in checks),
            "urls": {
                "mcp": "%s/mcp" % base,
                "metadata": "%s/.well-known/oauth-protected-resource" % base,
                "health": "%s/mcp/health" % base,
            },
            "qr": self._qr_data_uri("%s/mcp" % base),
            "status": self._connection_status(),
            "connections": self._connections(),
            "prompts": self._starter_prompts(),
            "clients": self._client_guides(base),
        }

    @api.model
    def _base_url(self):
        return self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "").rstrip("/")

    # ------------------------------------------------------------- readiness
    @api.model
    def _readiness_checks(self, base):
        """Each check states the problem *and* where to fix it.

        `fail` blocks connecting at all; `warn` will still work but is worth
        knowing about. Anything the user cannot act on is not a check.
        """
        Param = self.env["ir.config_parameter"].sudo()
        checks = []

        # 1. Public HTTPS base URL - the single most common cause of failure.
        if not base:
            checks.append(self._check(
                "base_url", "fail", _("Odoo's public address is not set"),
                _("AI clients need a public HTTPS address to reach this server "
                  "and to complete sign-in."),
                _("Set web.base.url"), "base_setup.action_general_configuration"))
        elif not base.startswith("https://"):
            checks.append(self._check(
                "base_url", "fail", _("Public address is not HTTPS"),
                _("Sign-in requires HTTPS. Your address is currently %s.") % base,
                _("Fix the address"), "base_setup.action_general_configuration"))
        elif any(host in base for host in
                 ("localhost", "127.0.0.1", ".local", "0.0.0.0")):
            checks.append(self._check(
                "base_url", "fail", _("Public address points at this machine"),
                _("%s is only reachable from this server, so no AI client can "
                  "connect to it.") % base,
                _("Fix the address"), "base_setup.action_general_configuration"))
        else:
            checks.append(self._check(
                "base_url", "ok", _("Public address looks right"), base))

        # 2. Reachability - cached; the network test runs on demand.
        state = Param.get_param(PARAM_REACH_STATE)
        checked_at = Param.get_param(PARAM_REACH_AT)
        if state == "ok":
            checks.append(self._check(
                "reach", "ok", _("Reachable from the internet"),
                _("Last checked %s.") % (checked_at or _("just now"))))
        elif state == "fail":
            checks.append(self._check(
                "reach", "fail", _("Could not reach this server from outside"),
                _("The address answered but not from the public internet, so "
                  "Claude and ChatGPT will not be able to connect. Check DNS, "
                  "your reverse proxy and any firewall.")))
        else:
            checks.append(self._check(
                "reach", "unknown", _("Reachability not tested yet"),
                _("Test that this server answers from the public internet "
                  "before handing the URL to anyone.")))

        # 3. Database selector - breaks the sign-in redirect.
        if config.get("list_db"):
            checks.append(self._check(
                "list_db", "fail", _("Database selector is enabled"),
                _("With multiple databases selectable, the sign-in redirect "
                  "cannot know which one to return to. Set list_db = False in "
                  "your odoo.conf and restart.")))
        else:
            checks.append(self._check(
                "list_db", "ok", _("Database selector is off"),
                _("Sign-in can complete cleanly.")))

        # 4/5. Something to actually govern.
        scopes = self.env["mcp.scope"].sudo().search_count([("active", "=", True)])
        if not scopes:
            checks.append(self._check(
                "scope", "fail", _("No governance scope exists"),
                _("A scope decides what an assistant may see and do. Without "
                  "one, nobody can be authorized."),
                _("Create a scope"), "mcp_governance_suite.mcp_scope_action"))
        else:
            checks.append(self._check(
                "scope", "ok", _("%s scope(s) configured") % scopes))

        lines = self.env["mcp.scope.line"].sudo().search_count(
            [("can_read", "=", True)])
        if not lines:
            checks.append(self._check(
                "models", "fail", _("No models are readable"),
                _("An assistant with no models in the permission matrix can "
                  "connect but can do nothing at all."),
                _("Open the matrix"),
                "mcp_governance_suite.mcp_scope_line_action"))
        else:
            checks.append(self._check(
                "models", "ok", _("%s model permission(s) set") % lines))

        # 6. Master switch.
        enabled = Param.get_param("mcp_governance_suite.enabled", "1")
        if str(enabled).lower() not in ("1", "true", "t", "yes"):
            checks.append(self._check(
                "enabled", "fail", _("The MCP server is switched off"),
                _("Nothing will answer until you turn it back on."),
                _("Open settings"),
                "mcp_governance_suite.mcp_config_settings_action"))
        return checks

    @api.model
    def _check(self, key, state, label, detail="", fix_label=None, fix_action=None):
        return {"key": key, "state": state, "label": label, "detail": detail,
                "fix_label": fix_label, "fix_action": fix_action}

    @api.model
    def test_reachability(self):
        """Fetch our own health endpoint from the outside and cache the verdict.

        Called explicitly, never from get_state(), so a slow or blackholed
        address can never hang the page.
        """
        Param = self.env["ir.config_parameter"].sudo()
        base = self._base_url()
        state = "fail"
        if base and requests:
            try:
                resp = requests.get("%s/mcp/health" % base, timeout=REACH_TIMEOUT)
                state = "ok" if resp.status_code == 200 else "fail"
            except Exception as exc:  # noqa: BLE001 - the verdict is the point
                _logger.info("MCP reachability test failed for %s: %s", base, exc)
        Param.set_param(PARAM_REACH_STATE, state)
        Param.set_param(PARAM_REACH_AT, fields.Datetime.to_string(
            fields.Datetime.now()))
        return self.get_state()

    # ------------------------------------------------------------ live status
    @api.model
    def _scoping_domain(self):
        """Admins see the whole instance; everyone else only themselves."""
        if self.env.user.has_group("mcp_governance_suite.group_mcp_admin"):
            return []
        return [("user_id", "=", self.env.uid)]

    @api.model
    def _connection_status(self):
        """Drives the "waiting…" → "connected" flip the moment a token lands."""
        scoping = self._scoping_domain()
        live = self.env["mcp.oauth.token"].sudo().search(
            [("revoked", "=", False),
             ("access_expires_at", ">", fields.Datetime.now())] + scoping)
        keys = self.env["mcp.api.key"].sudo().search_count(
            [("key_hash", "!=", False), ("active", "=", True)] + scoping)
        if not live and not keys:
            return {"state": "waiting",
                    "headline": _("Waiting for your first connection…"),
                    "detail": _("Leave this page open. It updates the moment an "
                                "assistant signs in.")}
        users = len(set(live.mapped("user_id").ids))
        # last_used is empty on a freshly issued token, and max() over a mixed
        # list of False and datetimes raises - filter before comparing.
        used = [d for d in live.mapped("last_used") if d]
        last = max(used) if used else None
        return {
            "state": "connected",
            "headline": _("Connected"),
            "detail": self._status_detail(users, keys, last),
        }

    @api.model
    def _status_detail(self, users, keys, last):
        bits = []
        if users:
            bits.append(_("%s user(s) signed in") % users)
        if keys:
            bits.append(_("%s API key(s)") % keys)
        if last:
            bits.append(_("last call %s") % self._ago(last))
        return " · ".join(bits)

    @api.model
    def _ago(self, when):
        delta = fields.Datetime.now() - when
        minutes = int(delta.total_seconds() // 60)
        if minutes < 1:
            return _("just now")
        if minutes < 60:
            return _("%s min ago") % minutes
        hours = minutes // 60
        if hours < 24:
            return _("%s hour(s) ago") % hours
        return _("%s day(s) ago") % (hours // 24)

    @api.model
    def _connections(self):
        """One row per live token. Users see their own; admins see everyone's."""
        tokens = self.env["mcp.oauth.token"].sudo().search(
            [("revoked", "=", False)] + self._scoping_domain(), limit=50)
        return [{
            "id": t.id,
            "user": t.user_id.name,
            "client": t.client_name or t.client_id,
            "scope": t.scope_id.name,
            "connected": fields.Datetime.to_string(t.create_date),
            "last_used": self._ago(t.last_used) if t.last_used else _("not yet used"),
            "expired": t.access_expires_at < fields.Datetime.now(),
        } for t in tokens]

    @api.model
    def revoke(self, token_id):
        """Disconnect one assistant. Users may only revoke their own."""
        token = self.env["mcp.oauth.token"].sudo().browse(int(token_id)).exists()
        if not token:
            return self.get_state()
        if token.user_id.id != self.env.uid and not self.env.user.has_group(
                "mcp_governance_suite.group_mcp_admin"):
            return self.get_state()
        token.action_revoke()
        return self.get_state()

    @api.model
    def revoke_all(self):
        """Kill switch. Admins cut everyone off; users cut off themselves."""
        if self.env.user.has_group("mcp_governance_suite.group_mcp_admin"):
            self.env["mcp.oauth.token"].sudo().search(
                [("revoked", "=", False)]).write({"revoked": True})
        else:
            self.env["mcp.oauth.token"].revoke_for_user(self.env.uid)
        return self.get_state()

    # ---------------------------------------------------------------- content
    @api.model
    def _starter_prompts(self):
        """Never advertise a write prompt when no scope can satisfy it."""
        writable = bool(self.env["mcp.scope"].sudo().search_count(
            [("active", "=", True), ("read_only", "=", False)]))
        return [text for needs_write, text in STARTER_PROMPTS
                if not needs_write or writable]

    @api.model
    def _client_guides(self, base):
        url = "%s/mcp" % base
        guides = []
        for key, guide in CLIENT_STEPS.items():
            entry = {"key": key, "name": guide["name"], "steps": guide["steps"],
                     "note": guide.get("note")}
            if guide.get("config"):
                entry["config"] = (
                    '{\n'
                    '  "mcpServers": {\n'
                    '    "odoo": {\n'
                    '      "url": "%s"\n'
                    '    }\n'
                    '  }\n'
                    '}' % url)
            guides.append(entry)
        return guides

    # --------------------------------------------------------------------- QR
    @api.model
    def _qr_data_uri(self, url):
        """Server-side QR so the page needs no external asset (store policy)."""
        if not qrcode or not url:
            return False
        try:
            img = qrcode.make(url, box_size=4, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return "data:image/png;base64,%s" % base64.b64encode(
                buf.getvalue()).decode()
        except Exception as exc:  # noqa: BLE001 - a missing QR is cosmetic
            _logger.info("QR generation failed: %s", exc)
            return False
