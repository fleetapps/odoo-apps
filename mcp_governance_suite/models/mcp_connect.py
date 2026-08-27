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
import datetime
import io
import json
import logging
from urllib.parse import quote, urlparse

from odoo import _, api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import config

from .mcp_scope import SUGGESTED_MODELS
from .mcp_url import base_url_report, public_base_url

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
PARAM_REACH_DETAIL = "mcp_governance_suite.reachability_detail"
REACH_TIMEOUT = 4
# How long a reachability verdict stands before the screen probes again. The
# test costs an outbound round trip plus three parameter writes, and the screen
# runs it on every mount; a deployment's public address does not change between
# two page views, so re-probing on each one is pure cost.
REACH_CACHE_MINUTES = 15

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
        "install": "vscode",
    },
    "cursor": {
        "name": "Cursor",
        "steps": [
            "Open Cursor → Settings → <b>MCP</b>.",
            "Click <b>Add new global MCP server</b>.",
            "Paste the configuration below, then sign in when prompted.",
        ],
        "config": True,
        "install": "cursor",
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

# The name the server registers itself under inside the client's config.
INSTALL_SERVER_NAME = "odoo"

# Where a starter prompt can be opened, once a connection exists. Both take the
# prompt as ?q= and only prefill the composer - the user still reads it and
# presses send, which is the correct amount of automation for something that is
# about to touch live ERP data.
#
# There is deliberately no equivalent for *setting up* Claude or ChatGPT.
# Neither has a connector-install deep link: claude:// only opens new/chat/
# project/code/cowork, and ChatGPT's connectors are added by hand under
# Developer Mode. A button that opened a chat saying "connect this MCP server"
# would produce an assistant explaining it cannot do that, under a label
# promising one-click setup - the same broken-first-minute failure the starter
# prompt filtering exists to prevent.
ASK_URLS = {
    "claude": ("Claude", "https://claude.ai/new?q=%s"),
    "chatgpt": ("ChatGPT", "https://chatgpt.com/?q=%s"),
}

# Shown as one-click chips, as (needs_write, model, text). A prompt is only
# offered when the permission matrix can actually satisfy it: the first thing a
# new user does is click one of these, and a chip that comes back "I don't have
# access to that" reads as a broken product, not as a permission to grant. A
# model of None means the prompt works against any scope.
STARTER_PROMPTS = [
    (False, None, "What can you do with my Odoo?"),
    (False, None, "What Odoo data can you see?"),
    (False, "res.partner", "Show me all customers from France"),
    (False, "product.template", "What do we sell, and at what price?"),
    # Gated on stock.quant, not product.product: without Inventory installed a
    # product has no stock figure to report, and a chip that fails in the
    # assistant reads as the product being broken.
    (False, "stock.quant", "Find products with stock below 10 units"),
    (False, "stock.picking", "Which deliveries are late?"),
    (False, "sale.order", "List today's sales orders over $1900"),
    (False, "sale.order", "Who are my top 10 customers by revenue this year?"),
    (False, "purchase.order", "What did we buy from each vendor this quarter?"),
    (False, "account.move", "Search for unpaid invoices from last month"),
    (False, "account.move", "Break down revenue by month for this year"),
    (False, "crm.lead", "Which open opportunities are worth the most?"),
    (False, "project.task", "What tasks are still open, and who has them?"),
    (True, "sale.order", "Create a draft sale order for Deco Addict"),
    (True, "res.partner", "Create partners from the attached document"),
]


class MCPConnect(models.TransientModel):
    _name = "mcp.connect"
    _description = "Connect your AI"

    # ------------------------------------------------------------------ state
    @api.model
    def get_state(self, with_qr=True):
        """Everything the Connect screen renders, in one round trip.

        Deliberately does no network I/O: this is polled, and a hanging outbound
        request would freeze the page. Reachability lives in test_reachability().

        ``with_qr`` exists because the QR is rendered server-side and is the
        single most expensive thing here, while the URL it encodes cannot
        change between two polls of the same page. The client asks for it once,
        on first load, and drops it from every poll after that. The default
        stays True so ``fix_base_url``/``revoke``/``set_writes`` and friends,
        which all return ``get_state()``, keep behaving exactly as before.
        """
        base = self._base_url()
        connections = self._connections()
        checks = self._readiness_checks(base)
        state = {
            "can_admin": self.env.user.has_group("mcp_governance_suite.group_mcp_admin"),
            "checks": checks,
            "ready": all(c["state"] != "fail" for c in checks),
            "urls": {
                "mcp": "%s/mcp" % base,
                "metadata": "%s/.well-known/oauth-protected-resource" % base,
                "health": "%s/mcp/health" % base,
            },
            "status": self._connection_status(),
            "connections": connections,
            "writes": self._writes_state(connections),
            "prompts": self._starter_prompts(),
            "clients": self._client_guides(base),
        }
        if with_qr:
            state["qr"] = self._qr_data_uri("%s/mcp" % base)
        return state

    @api.model
    def _base_url(self):
        """The address to show, which is the one clients will actually use.

        Never read ``web.base.url`` directly here: on a TLS-terminating proxy
        it routinely holds an ``http://`` spelling of the right host, and
        showing that is how a user ends up pasting a URL into Claude that
        Claude then refuses. See models/mcp_url.py.
        """
        return public_base_url(self.env)

    # ------------------------------------------------------------- readiness
    @api.model
    def _readiness_checks(self, base):
        """Each check states the problem *and* where to fix it.

        `fail` blocks connecting at all; `warn` will still work but is worth
        knowing about. Anything the user cannot act on is not a check.
        """
        Param = self.env["ir.config_parameter"].sudo()
        report = base_url_report(self.env)
        checks = []

        # 1. Public HTTPS address - the single most common cause of failure.
        checks.extend(self._base_url_checks(base, report))

        # 2. Reachability - cached; the network test runs on demand.
        state = Param.get_param(PARAM_REACH_STATE)
        checked_at = Param.get_param(PARAM_REACH_AT)
        detail = Param.get_param(PARAM_REACH_DETAIL)
        if state == "ok":
            checks.append(self._check(
                "reach", "ok", _("Reachable from the internet"),
                detail or _("Last checked %s.") % (checked_at or _("just now"))))
        elif state == "fail":
            checks.append(self._check(
                "reach", "fail", _("Could not reach this server from outside"),
                detail or _("The address answered but not from the public "
                            "internet, so Claude and ChatGPT will not be able "
                            "to connect. Check DNS, your reverse proxy and any "
                            "firewall.")))
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

        # `scope_id.active` matters: an archived scope's rows are not reachable
        # by anyone, and counting them here contradicted _starter_prompts(),
        # which has always filtered on it - so the checklist could read "12
        # model permission(s) set" beside a prompt list that offered nothing.
        lines = self.env["mcp.scope.line"].sudo().search_count(
            [("can_read", "=", True), ("scope_id.active", "=", True)])
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

        # 6. Business models, as opposed to merely *some* models.
        checks.extend(self._business_model_checks())

        # 7. Master switch. The happy path needs a row of its own: without one
        # the list simply ends, and a checklist that stops early reads as a
        # checklist that has not finished running.
        enabled = Param.get_param("mcp_governance_suite.enabled", "1")
        if str(enabled).lower() not in ("1", "true", "t", "yes"):
            checks.append(self._check(
                "enabled", "fail", _("AI access is switched off"),
                _("Nothing will answer until you turn it back on."),
                _("Open settings"),
                "mcp_governance_suite.mcp_config_settings_action"))
        else:
            checks.append(self._check(
                "enabled", "ok", _("AI access is switched on"),
                _("This Odoo is ready to answer.")))
        return checks

    @api.model
    def _business_model_checks(self):
        """Warn when the scope covers Odoo's plumbing but nothing a business asks about.

        A fresh install seeds four `base` models, because a data file that
        names ``sale.order`` cannot be installed on a database without Sales.
        So a database upgraded from an earlier version connects perfectly and
        then cannot answer a single real question - the assistant can describe
        countries and currencies. The install hook fixes new databases; this
        row plus its button fixes the ones that already exist.

        Only raised when *nothing* suggested is readable. Once an
        administrator has opened the scope onto their business, a scope they
        have deliberately kept narrow is their decision, not a defect to nag
        about on every page view.
        """
        scope = self.env.user.sudo().mcp_effective_scope()
        if not scope:
            return []  # the "no scope" check above already covers this
        readable = set(scope.readable_model_names())
        installed = [m for m in SUGGESTED_MODELS if m in self.env]
        if not installed or readable & set(installed):
            return []
        fix = self.env.user.has_group("mcp_governance_suite.group_mcp_admin")
        return [self._check(
            "models_thin", "warn",
            _("Your assistant cannot see your business data yet"),
            _("Right now it can only see contacts, companies, countries and "
              "currencies. Ask it about sales, invoices or stock and the "
              "answer comes back empty."),
            _("Let it see my business data") if fix else None,
            None, "add_suggested_models" if fix else None)]

    @api.model
    def _base_url_checks(self, base, report):
        """Everything that can be wrong about the address, said plainly.

        Two separate failures hide here and they need separate rows, because a
        user who fixes only the visible one watches it break again a day later:

        * the address is wrong *now* (missing, plain http, or pointing at this
          machine) - nothing can connect until it is fixed;
        * the address is right now but Odoo will overwrite it. Odoo rewrites
          ``web.base.url`` to wherever an administrator last signed in from
          unless ``web.base.url.freeze`` is set, so behind a proxy that does
          not announce https the parameter gets reset to an http spelling on
          the next admin login. Connecting then stops working for no reason
          the user can see, which is the single worst failure this module has.
        """
        checks = []
        fix = _("Fix it for me") if self._can_fix_base_url() else None
        method = "fix_base_url" if fix else None

        if not base:
            checks.append(self._check(
                "base_url", "fail", _("Odoo's public address is not set"),
                _("AI clients need a public HTTPS address to reach this server "
                  "and to complete sign-in."),
                _("Set web.base.url"), "base_setup.action_general_configuration"))
            return checks
        # Substring matching here would flag a perfectly good erp.locality.com,
        # so compare the host itself.
        host = urlparse(base).hostname or ""
        if report["loopback"] or host == "0.0.0.0" or host.endswith(".local"):
            checks.append(self._check(
                "base_url", "fail", _("Public address points at this machine"),
                _("%s is only reachable from this server, so no AI client can "
                  "connect to it.") % base,
                _("Fix the address"), "base_setup.action_general_configuration"))
            return checks
        if not report["secure"]:
            checks.append(self._check(
                "base_url", "fail", _("This server is not on HTTPS"),
                _("Sign-in requires HTTPS and every AI client refuses a plain "
                  "http address. This server sees itself as %s. If it really is "
                  "behind an HTTPS proxy, the proxy is not passing "
                  "X-Forwarded-Proto — until it does, set the address "
                  "explicitly.") % base,
                _("Set the address"), "base_setup.action_general_configuration"))
            return checks

        checks.append(self._check("base_url", "ok", _("Address is HTTPS"), base))

        # The drift trap: right today, reset by Odoo tomorrow.
        if not report["frozen"] and not report["override"]:
            if report["configured"] and not report["configured"].startswith("https://"):
                checks.append(self._check(
                    "base_url_drift", "warn",
                    _("Odoo has this server's address recorded as plain http"),
                    _("Clients are being given the correct %(good)s, but Odoo "
                      "itself has %(bad)s stored in web.base.url and puts that "
                      "in the emails it sends. Worse, it rewrites that value "
                      "from wherever an administrator last signed in, so it "
                      "will keep coming back until web.base.url.freeze is set.",
                      good=base, bad=report["configured"]),
                    fix, None, method, True))
            elif report["configured_differs"]:
                checks.append(self._check(
                    "base_url_drift", "warn",
                    _("Odoo's recorded address is not the one clients use"),
                    _("Clients reach %(good)s; web.base.url says %(bad)s. Odoo "
                      "rewrites that value on each administrator login, so pin "
                      "it before it drifts somewhere that breaks sign-in.",
                      good=base, bad=report["configured"]),
                    fix, None, method, True))
            elif not report["proxy_mode"] and report["forwarded_scheme"]:
                checks.append(self._check(
                    "base_url_drift", "warn",
                    _("A proxy is in front of Odoo but proxy_mode is off"),
                    _("This module reads the forwarded headers itself, so "
                      "connecting works. The rest of Odoo does not: set "
                      "proxy_mode = True in odoo.conf so links, emails and "
                      "attachments get the right address too."),
                    fix, None, method, True))
        return checks

    @api.model
    def _can_fix_base_url(self):
        """Writing web.base.url is a system-wide change, so gate it on Settings."""
        return self.env.user.has_group("base.group_system")

    @api.model
    def fix_base_url(self):
        """Pin web.base.url to the address clients actually use, and freeze it.

        The one-click version of the two-line fix, because the manual route -
        find developer mode, find System Parameters, know that a second
        parameter called web.base.url.freeze exists at all - is where people
        give up.
        """
        if not self._can_fix_base_url():
            raise AccessError(_("Only a Settings administrator can change "
                                "this server's public address."))
        base = self._base_url()
        if base:
            Param = self.env["ir.config_parameter"].sudo()
            Param.set_param("web.base.url", base)
            Param.set_param("web.base.url.freeze", "True")
            _logger.info("MCP: pinned web.base.url to %s and froze it", base)
        return self.get_state()

    @api.model
    def _check(self, key, state, label, detail="", fix_label=None,
               fix_action=None, fix_method=None, fix_retest=False):
        """One row. `fix_action` opens a screen; `fix_method` does the fix here.

        `fix_retest` says whether applying the fix invalidates the reachability
        verdict. Changing the public address does; adding models to the matrix
        does not, and re-probing after it would only spend a round trip to
        restate what the screen already knows.
        """
        return {"key": key, "state": state, "label": label, "detail": detail,
                "fix_label": fix_label, "fix_action": fix_action,
                "fix_method": fix_method, "fix_retest": fix_retest}

    @api.model
    def test_reachability(self, force=False):
        """Connect to ourselves the way an AI client would, and cache the verdict.

        Called explicitly, never from get_state(), so a slow or blackholed
        address can never hang the page.

        Every mount of the screen calls this, so an unconditional probe means an
        outbound round trip plus three parameter writes for every page view, by
        every user, forever. A verdict younger than REACH_CACHE_MINUTES is
        therefore reused untouched. The Re-test button and anything that has
        just changed the address pass ``force=True``, which is the only case
        where the answer can actually have changed.

        This walks the real client handshake rather than just pinging health:
        a client's first move is to fetch the RFC 9728 metadata and read the
        authorization server out of it, and it gives up right there if that
        document names an ``http://`` endpoint. Checking only ``/mcp/health``
        would have called this deployment reachable while every client refused
        it - which is exactly the failure this screen exists to prevent.
        """
        Param = self.env["ir.config_parameter"].sudo()
        if not force and self._reach_verdict_is_fresh(Param):
            return self.get_state()
        state, detail = self._probe(self._base_url())
        Param.set_param(PARAM_REACH_STATE, state)
        Param.set_param(PARAM_REACH_DETAIL, detail)
        Param.set_param(PARAM_REACH_AT, fields.Datetime.to_string(
            fields.Datetime.now()))
        return self.get_state()

    @api.model
    def _reach_verdict_is_fresh(self, Param):
        """Is there a verdict, and is it younger than the cache window?"""
        if Param.get_param(PARAM_REACH_STATE) not in ("ok", "fail"):
            return False
        try:
            checked_at = fields.Datetime.to_datetime(
                Param.get_param(PARAM_REACH_AT))
        except ValueError:  # a hand-edited parameter must not break the page
            return False
        if not checked_at:
            return False
        age = fields.Datetime.now() - checked_at
        return age < datetime.timedelta(minutes=REACH_CACHE_MINUTES)

    @api.model
    def _probe(self, base):
        """Return ("ok"|"fail", plain-language detail)."""
        if not base:
            return "fail", _("No public address to test.")
        if not requests:
            return "fail", _("The python-requests library is missing on this "
                             "server, so this test cannot run.")
        try:
            health = requests.get("%s/mcp/health" % base, timeout=REACH_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - the verdict is the point
            _logger.info("MCP reachability test failed for %s: %s", base, exc)
            return "fail", _("%(url)s did not answer (%(err)s). Check DNS, your "
                             "reverse proxy and any firewall.",
                             url=base, err=type(exc).__name__)
        if health.status_code != 200:
            return "fail", _("%(url)s answered HTTP %(code)s instead of 200.",
                             url="%s/mcp/health" % base, code=health.status_code)

        meta_url = "%s/.well-known/oauth-protected-resource" % base
        try:
            meta = requests.get(meta_url, timeout=REACH_TIMEOUT).json()
        except Exception as exc:  # noqa: BLE001
            _logger.info("MCP metadata probe failed for %s: %s", meta_url, exc)
            return "fail", _("This server answered, but %(url)s did not return "
                             "usable sign-in metadata, so no client can "
                             "authorize.", url=meta_url)
        advertised = (meta.get("authorization_servers") or [""])[0]
        if not advertised.startswith("https://"):
            return "fail", _("This server answered, but it tells clients to "
                             "sign in at %(bad)s. Every AI client refuses a "
                             "plain http sign-in address, so the connection "
                             "fails with an unhelpful error. Pin the public "
                             "address to fix it.", bad=advertised or base)
        if meta.get("resource") and not str(meta["resource"]).startswith(base):
            return "fail", _("This server answered as %(seen)s but identifies "
                             "itself to clients as %(said)s. The two must "
                             "match or tokens are rejected as being for a "
                             "different server.",
                             seen=base, said=meta["resource"])
        return "ok", _("Answered over HTTPS and advertised sign-in at %s.") % advertised

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
            # What it can actually do, not just which scope governs it: the two
            # come apart whenever the scope was widened after connecting.
            "access": t.access_label,
            "can_write": t.can_write,
            "hint": t.reconnect_reason or "",
            "connected": fields.Datetime.to_string(t.create_date),
            "last_used": self._ago(t.last_used) if t.last_used else _("not yet used"),
            "expired": t.access_expires_at < fields.Datetime.now(),
        } for t in tokens]

    @api.model
    def revoke(self, token_id):
        """Disconnect one assistant. Users may only revoke their own.

        Returns ``{state, ok}`` rather than bare state: both refusals below are
        silent, so the screen used to announce "Assistant disconnected." over a
        connection that was still very much connected. On a security control
        that is not a cosmetic problem.
        """
        token = self.env["mcp.oauth.token"].sudo().browse(int(token_id)).exists()
        if not token:
            return {"state": self.get_state(), "ok": False,
                    "message": _("That connection no longer exists.")}
        if token.user_id.id != self.env.uid and not self.env.user.has_group(
                "mcp_governance_suite.group_mcp_admin"):
            return {"state": self.get_state(), "ok": False,
                    "message": _("You can only disconnect your own assistants.")}
        token.action_revoke()
        return {"state": self.get_state(), "ok": True,
                "message": _("Assistant disconnected.")}

    @api.model
    def revoke_all(self):
        """Kill switch. Admins cut everyone off; users cut off themselves."""
        if self.env.user.has_group("mcp_governance_suite.group_mcp_admin"):
            self.env["mcp.oauth.token"].sudo().search(
                [("revoked", "=", False)]).write({"revoked": True})
        else:
            self.env["mcp.oauth.token"].revoke_for_user(self.env.uid)
        return self.get_state()

    # ----------------------------------------------------- what it may do
    @api.model
    def _writes_state(self, connections):
        """Whether this user's assistant may change anything, and what it costs.

        Read-only versus read-and-write is the decision every buyer of this
        module actually cares about, and until now the only place to make it
        was a checkbox on a scope form reached through Permissions → Scopes.
        Somebody evaluating the product never found it and concluded the
        connector could not write at all.
        """
        user = self.env.user
        can_toggle = user.has_group("mcp_governance_suite.group_mcp_admin")
        can_approve = user.has_group("mcp_governance_suite.group_mcp_approver")
        # An approver watches the whole queue; everyone else sees only requests
        # they themselves are waiting on.
        domain = [("state", "=", "pending")]
        if not can_approve:
            domain.append(("user_id", "=", self.env.uid))
        pending = self.env["mcp.approval.request"].sudo().search_count(domain)

        scope = user.sudo().mcp_effective_scope()
        # Changing the scope never reaches a live connection: both the
        # governance scope and the granted OAuth scopes are frozen at consent.
        # mcp.oauth.token computes exactly why, so surface its words rather
        # than writing a second, drifting explanation here.
        hints = [c["hint"] for c in connections if c["hint"]]
        return {
            "enabled": bool(scope) and not scope.read_only,
            "requires_approval": bool(scope) and scope.require_approval
                                 and not scope.read_only,
            "scope_name": scope.name if scope else "",
            "can_toggle": can_toggle,
            "can_approve": can_approve,
            "pending": pending,
            "needs_reconnect": bool(hints),
            "reconnect_hint": hints[0] if hints else "",
        }

    @api.model
    def set_writes(self, enabled):
        """Flip the effective scope between read-only and governed writes.

        Deliberately leaves ``require_approval`` alone: turning writes on must
        not quietly also turn the human approval gate off, and an administrator
        who wants unattended writes should have to say so on the scope itself.
        """
        if not self.env.user.has_group("mcp_governance_suite.group_mcp_admin"):
            raise AccessError(_(
                "Only an AI MCP administrator can change what assistants "
                "are allowed to do."))
        scope = self.env.user.sudo().mcp_effective_scope()
        if scope:
            scope.read_only = not enabled
            _logger.info("MCP: %s set scope '%s' to %s", self.env.user.login,
                         scope.name, "read-write" if enabled else "read-only")
        return self.get_state()

    @api.model
    def add_suggested_models(self):
        """Open this user's effective scope onto the apps this database has.

        The same thing the install hook does for a new database, offered as a
        button so an existing one is never widened without somebody asking.
        """
        if not self.env.user.has_group("mcp_governance_suite.group_mcp_admin"):
            raise AccessError(_(
                "Only an AI MCP administrator can add models to the "
                "permission matrix."))
        scope = self.env.user.sudo().mcp_effective_scope()
        if scope:
            added = scope.add_models(SUGGESTED_MODELS, preset="read")
            _logger.info("MCP: %s added %s suggested model(s) to '%s'",
                         self.env.user.login, len(added), scope.name)
        return self.get_state()

    # -------------------------------------------------------------- self test
    @api.model
    def run_self_test(self):
        """Ask a real question, through the real engine, as the real user.

        Everything else on this screen is a precondition. This is the only
        thing that proves the whole chain - scope, matrix, the user's own
        access rights, the audit trail - actually answers, and it does so
        without asking anyone to go and set up Claude first. It runs through
        ``call_tool``, so it passes every gate a genuine call passes and lands
        in the audit log honestly, tagged as a self-test rather than
        masquerading as something an assistant did.
        """
        scope = self.env.user.sudo().mcp_effective_scope()
        if not scope:
            return self._self_test_result(False, _(
                "No permissions apply to you yet, so there is nothing to "
                "test. An administrator sets those up under AI MCP → "
                "Permissions."))
        readable = scope.line_ids.filtered("can_read")
        if not readable:
            return self._self_test_result(False, _(
                "\"%s\" cannot read anything, so an assistant would have "
                "nothing to answer with.") % scope.name)
        # The first row the *user* can actually read, not simply the first row.
        # The matrix is only half the permission; their own Odoo access rights
        # are the other half, and testing a model they were never meant to see
        # would report a failure that is really the system working correctly.
        line = next((l for l in readable if self._user_can_read(l.model_name)),
                    None)
        if not line:
            return self._self_test_result(False, _(
                "Your own Odoo access rights do not cover anything in \"%s\". "
                "An assistant runs as you, so it can never see more than you "
                "can — ask an administrator for access to the records you "
                "need.") % scope.name)

        model = line.model_name
        label = line.model_id.name or model
        result = self.env["mcp.engine"].call_tool(
            scope, "search_records", {"model": model, "limit": 5},
            {"transport": "selftest"})
        payload = json.loads(result["content"][0]["text"])
        if result.get("isError"):
            return self._self_test_result(
                False, payload.get("message") or _("The test call failed."),
                model=model)

        records = payload.get("records") or []
        if not records:
            return self._self_test_result(
                True, _("It works. There are no %s records to show yet.")
                % label.lower(), model=model)
        return self._self_test_result(
            True,
            _("It works. Your assistant read %(count)s %(model)s record(s), "
              "running as you. The call is logged in My AI Activity.",
              count=len(records), model=label.lower()),
            model=model,
            names=[str(r.get("display_name") or r.get("id")) for r in records])

    @api.model
    def _self_test_result(self, ok, message, model="", names=None):
        """`names` is always present so the template can read its length."""
        names = names or []
        return {"ok": ok, "message": message, "model": model,
                "count": len(names), "names": names}

    # ---------------------------------------------------------------- content
    @api.model
    def _starter_prompts(self):
        """Never advertise a prompt the current permissions would refuse.

        Checked against the models in the matrix, not merely against whether
        writing is possible somewhere. The out-of-the-box scope covers four
        `base` models, so a fresh install used to offer "list today's sales
        orders" as a headline suggestion and have it fail - the worst possible
        first minute with a connector whose whole promise is that it just works.
        """
        Line = self.env["mcp.scope.line"].sudo()
        # `active` on both models means archived rows and scopes drop out here.
        readable = set(Line.search(
            [("can_read", "=", True), ("scope_id.active", "=", True)]
        ).mapped("model_name"))
        writable = set(Line.search(
            ["&", ("scope_id.active", "=", True), ("scope_id.read_only", "=", False),
             "|", ("can_create", "=", True), ("can_write", "=", True)]
        ).mapped("model_name"))
        return [{"text": text, "ask": self._ask_links(text)}
                for needs_write, model, text in STARTER_PROMPTS
                if model is None
                or (model in (writable if needs_write else readable)
                    and self._user_can_read(model))]

    @api.model
    def _user_can_read(self, model):
        """Can *this* user read this model, on top of the matrix allowing it?

        The matrix is only half the permission; the reader's own Odoo access
        rights are the other half, and a scope opened onto the whole business
        will list models plenty of people cannot see. Without this, a
        salesperson is offered a stock question that comes back refused — the
        exact failure the prompt filtering exists to prevent.
        """
        if model not in self.env:
            return False
        return self.env[model].has_access("read")

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
                    '    "%s": {\n'
                    '      "url": "%s"\n'
                    '    }\n'
                    '  }\n'
                    '}' % (INSTALL_SERVER_NAME, url))
            install = self._install_url(guide.get("install"), url)
            if install:
                entry["install_url"] = install
                entry["install_label"] = _("Add to %s") % guide["name"]
            guides.append(entry)
        return guides

    @api.model
    def _install_url(self, kind, url):
        """A real one-click install link, for the two clients that have one.

        VS Code documents ``vscode:mcp/install?<url-encoded JSON>`` where the
        JSON is the server object with its name folded in. Cursor's equivalent
        is not in its own deeplink reference but is the de-facto format every
        "Add to Cursor" button in the wild uses: the server object alone,
        base64-encoded, with the name passed separately.

        Both hand the config to the client, which shows its own approval
        dialog before writing anything - so this saves typing, not consent.
        Returns False for a client with no such link rather than inventing one.
        """
        if not kind or not url:
            return False
        if kind == "vscode":
            # mcp.json's own remote-server shape, with the name folded in.
            payload = {"name": INSTALL_SERVER_NAME, "type": "http", "url": url}
            return "vscode:mcp/install?%s" % quote(
                json.dumps(payload, separators=(",", ":")), safe="")
        if kind == "cursor":
            # Cursor documents a remote server as {"url": ...} with no type
            # key, and the name travels as its own parameter.
            blob = base64.urlsafe_b64encode(
                json.dumps({"url": url}, separators=(",", ":")).encode()).decode()
            return ("cursor://anysphere.cursor-deeplink/mcp/install"
                    "?name=%s&config=%s" % (INSTALL_SERVER_NAME, blob))
        return False

    @api.model
    def _ask_links(self, prompt):
        """Open an assistant with this prompt already typed.

        Offered on the starter prompts rather than on setup, because that is
        the point at which it is honest: a connection exists by then, so the
        assistant can actually answer. Canned module constants only - never put
        record data in a query string, it travels to a third party and lands in
        their logs.
        """
        return [{"key": key, "name": name, "url": pattern % quote(prompt, safe="")}
                for key, (name, pattern) in ASK_URLS.items()]

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
