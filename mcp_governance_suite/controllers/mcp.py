# -*- coding: utf-8 -*-
"""MCP endpoint - Streamable HTTP transport, JSON-RPC 2.0 messages.

This server is **dual-era**: it speaks both shapes of the protocol.

* **Modern** (`2026-07-28`) carries the protocol version, client identity and
  capabilities as per-request `_meta` plus mirrored HTTP headers. There is no
  handshake and no session; `server/discover` reports what we support.
* **Legacy** (`2025-06-18` and earlier) opens with an `initialize` handshake.

Supporting only one era breaks the other outright - the spec's compatibility
matrix scores Modern-client/Legacy-server and Legacy-client/Modern-server both
as failures - so a connector that wants to work with today's *and* yesterday's
clients has to answer both. A dual-era server picks its behaviour from how the
client opens: modern `_meta` means modern, an `initialize` call means legacy.

Transport: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
Versioning: https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning

Authorization: a Bearer credential that is either
* an MCP API key (mcp.api.key), or
* an OAuth 2.1 access token (mcp.oauth.token) - the recommended path.

Either way the request env is switched to the acting user before any tool runs,
so ir.model.access + ir.rule enforce underneath the MCP governance scope. On a
missing/invalid token we answer 401 with a WWW-Authenticate header pointing at
the RFC 9728 resource metadata, which is how MCP clients discover OAuth.
"""
import base64
import binascii
import json
import logging
import secrets

from odoo import fields, http
from odoo.http import request
from odoo.modules.module import get_manifest

from .oauth import (
    SCOPE_READ,
    SCOPE_WRITE,
    base_url,
    accepted_resources,
    normalize_scopes,
    param_enabled,
    writes_enabled,
)
from ..models.mcp_engine import MCPInsufficientScope
from ..models.mcp_url import public_base_url
from ..models.tools_crypto import hash_secret

_logger = logging.getLogger(__name__)

MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)
LEGACY_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")
SUPPORTED_PROTOCOL_VERSIONS = MODERN_PROTOCOL_VERSIONS + LEGACY_PROTOCOL_VERSIONS
LATEST_LEGACY_VERSION = LEGACY_PROTOCOL_VERSIONS[0]

# Read from the manifest rather than restated here: this is the version every
# connected client sees, and a hand-maintained copy silently goes stale on the
# release it matters most for.
SERVER_INFO = {
    "name": "odoo-mcp-governance",
    "version": get_manifest("mcp_governance_suite").get("version", ""),
}
SERVER_INSTRUCTIONS = (
    "AI MCP Governance Suite. Every action runs as your Odoo user and is "
    "audited. Use list_capabilities to discover what you can do. Odoo "
    "conventions: filters are domains (lists of ('field','operator',value) "
    "triples with '&'/'|' prefix operators); read with search_records rather "
    "than fetching ids then reading them; display names come from the model's "
    "_rec_name; records are company-scoped, so results reflect the companies "
    "you have access to; most business documents start in a draft state and "
    "must be confirmed or posted to take effect."
)

META_PREFIX = "io.modelcontextprotocol/"
META_VERSION = META_PREFIX + "protocolVersion"

# JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
# MCP protocol-defined codes
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022
# Implementation-defined (JSON-RPC reserves -32000..-32099 for servers). The
# authoritative signal for a scope failure is the HTTP 403 + WWW-Authenticate;
# this code just gives the body something honest to carry.
INSUFFICIENT_SCOPE = -32003

# How stale `last_used` may get before it is rewritten. The field answers
# "is this connection still in use", so a minute is ample, and stamping every
# request makes concurrent tool calls fight over one row.
LAST_USED_SECONDS = 60

# Methods whose Mcp-Name header mirrors a body value.
NAME_HEADER_SOURCES = {
    "tools/call": "name",
    "prompts/get": "name",
    "resources/read": "uri",
}


def _allowed_origins(env):
    """Origins permitted to call the MCP endpoint from a browser.

    Empty by default: MCP clients are not browsers, and echoing an arbitrary
    Origin is exactly the DNS-rebinding hole the transport spec calls out.
    """
    raw = env["ir.config_parameter"].sudo().get_param(
        "mcp_governance_suite.allowed_origins", "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _origin_allowed(env, origin):
    """Is this browser Origin allowed to drive the endpoint?

    Compared against the *public* origin, not ``host_url``. Behind a
    TLS-terminating proxy those two disagree on the scheme, so matching on
    ``host_url`` alone rejects the server's own web client with
    ``forbidden_origin`` - a 403 whose text gives no hint that the cause is a
    missing X-Forwarded header. Both spellings of our own host are treated as
    ours; an origin is still only ever echoed back if it matched here.
    """
    if not origin:
        return True  # non-browser client: no Origin header to validate
    origin = origin.rstrip("/")
    ours = {request.httprequest.host_url.rstrip("/"), public_base_url(env)}
    for known in list(ours):
        if known.startswith("https://"):
            ours.add("http://" + known[len("https://"):])
        elif known.startswith("http://"):
            ours.add("https://" + known[len("http://"):])
    return origin in ours or origin in _allowed_origins(env)


def cors_headers(origin=None):
    headers = [
        ("Access-Control-Allow-Methods", "POST, OPTIONS"),
        ("Access-Control-Allow-Headers",
         "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Method, Mcp-Name"),
        ("Access-Control-Expose-Headers", "WWW-Authenticate"),
        ("Access-Control-Max-Age", "86400"),
    ]
    # Echo only an origin we actually trust; never a blanket wildcard.
    if origin and _origin_allowed(request.env, origin):
        headers.append(("Access-Control-Allow-Origin", origin.rstrip("/")))
        headers.append(("Vary", "Origin"))
    return headers


def decode_header_value(value):
    """Undo the `=?base64?...?=` sentinel the transport uses for unsafe values."""
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[len("=?base64?"):-len("?=")]).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return value
    return value


class MCPController(http.Controller):

    # ------------------------------------------------------------------ helpers
    def _origin(self):
        return request.httprequest.headers.get("Origin")

    def _json(self, data, status=200, extra_headers=None):
        return request.make_json_response(
            data, status=status,
            headers=cors_headers(self._origin()) + (extra_headers or []))

    def _resource_metadata_url(self):
        return base_url() + "/.well-known/oauth-protected-resource"

    def _challenge_scopes(self):
        """Scopes to name in the initial 401 challenge.

        MCP 2026-07-28 "Scope Selection Strategy": a client MUST treat the
        challenge scope set as authoritative and picks it ahead of
        `scopes_supported`. Naming only `odoo:read` here therefore pins every
        connection read-only for good - the client asks for exactly what we
        challenged for, gets a read-only token, and then never sees a write
        tool to trip the step-up flow with. So advertise write too, whenever
        this deployment has a scope that could actually grant it; the consent
        screen and the governance scope remain the real gates.
        """
        scopes = [SCOPE_READ]
        if writes_enabled(request.env):
            scopes.append(SCOPE_WRITE)
        return scopes

    def _challenge(self, params):
        """Build a RFC 6750 Bearer challenge from ordered key/value pairs.

        Values are quoted-string, so an embedded quote or backslash would let a
        description break out and forge extra parameters. Escape both.
        """
        def quote(value):
            return str(value).replace("\\", "\\\\").replace('"', '\\"')
        return "Bearer " + ", ".join(
            '%s="%s"' % (k, quote(v)) for k, v in params)

    def _unauthorized(self):
        """401 with the RFC 9728 pointer so clients can start the OAuth dance.

        `scope` tells the client what to ask for up front, which is what stops
        it requesting everything or having to guess.
        """
        challenge = self._challenge([
            ("resource_metadata", self._resource_metadata_url()),
            ("scope", " ".join(self._challenge_scopes())),
        ])
        return self._json({"error": "unauthorized"}, status=401,
                          extra_headers=[("WWW-Authenticate", challenge)])

    def _insufficient_scope(self, mid, required, description=None):
        """403 telling the client exactly which scopes to step up to."""
        challenge = self._challenge([
            ("error", "insufficient_scope"),
            ("scope", " ".join(required)),
            ("resource_metadata", self._resource_metadata_url()),
        ] + ([("error_description", description)] if description else []))
        return self._json({
            "jsonrpc": "2.0", "id": mid,
            "error": {"code": INSUFFICIENT_SCOPE,
                      "message": description or "Insufficient scope.",
                      "data": {"required_scopes": required}},
        }, status=403, extra_headers=[("WWW-Authenticate", challenge)])

    def _authenticate(self):
        """Resolve the Bearer credential to (scope, audit_ctx); switch user.

        Returns None when authentication fails.
        """
        auth = request.httprequest.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        env = request.env

        # 1) MCP API key (headless path)
        if param_enabled(env, "apikey_enabled", True):
            key = env["mcp.api.key"].mcp_authenticate(token)
            if key:
                self._become(key.user_id.id)
                self._touch(key)
                return key.scope_id, {
                    "api_key_id": key.id, "transport": "apikey",
                    # API keys predate OAuth scopes; the governance scope is
                    # the only gate, so do not apply a scope challenge.
                    "granted_scopes": None,
                    "remote_addr": request.httprequest.remote_addr}

        # 2) OAuth 2.1 access token (recommended path)
        if param_enabled(env, "oauth_enabled", True):
            tok = env["mcp.oauth.token"].sudo().search(
                [("access_token_hash", "=", hash_secret(token))], limit=1)
            if tok and tok.is_access_valid(accepted_resources=accepted_resources()):
                self._become(tok.user_id.id)
                self._touch(tok)
                scope = tok.scope_id or tok.user_id.mcp_effective_scope()
                return scope, {
                    "oauth_token_id": tok.id, "transport": "oauth",
                    "granted_scopes": normalize_scopes(tok.scope),
                    "remote_addr": request.httprequest.remote_addr}
        return None

    def _touch(self, credential):
        """Stamp last_used, but not on literally every request.

        An assistant answering one question makes several tool calls in quick
        succession, and stamping each one turned every read into a write on the
        same row — which under concurrency produces

            ERROR: could not serialize access due to concurrent update

        Odoo retries and the call still succeeds, so it reads as log noise
        rather than a fault, but it is a self-inflicted write conflict on the
        hottest row in the module. The field answers "is this connection still
        in use", which minute-level accuracy satisfies completely.
        """
        now = fields.Datetime.now()
        previous = credential.last_used
        if previous and (now - previous).total_seconds() < LAST_USED_SECONDS:
            return
        credential.sudo().last_used = now

    def _become(self, uid):
        """Run the rest of the request as the principal, with all their companies
        visible (multi-company aware)."""
        user = request.env["res.users"].sudo().browse(uid)
        request.update_env(
            user=uid,
            context=dict(request.env.context,
                         allowed_company_ids=user.company_ids.ids or [user.company_id.id]))

    # ------------------------------------------------------------------- routes
    @http.route("/mcp/health", type="http", auth="none", methods=["GET"],
                csrf=False, save_session=False)
    def health(self, **kw):
        env = request.env
        return self._json({
            "status": "ok",
            "server": SERVER_INFO,
            "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "authMethods": [m for m, on in (
                ("oauth", param_enabled(env, "oauth_enabled", True)),
                ("apikey", param_enabled(env, "apikey_enabled", True))) if on],
        })

    @http.route(["/mcp", "/mcp/v1"], type="http", auth="none",
                methods=["OPTIONS"], csrf=False, save_session=False)
    def mcp_preflight(self, **kw):
        return request.make_response(
            "", headers=cors_headers(self._origin()), status=204)

    @http.route(["/mcp", "/mcp/v1"], type="http", auth="none",
                methods=["GET", "DELETE"], csrf=False, save_session=False)
    def mcp_unsupported_method(self, **kw):
        # 2026-07-28 removed the GET stream and protocol-level sessions, so
        # neither the standalone SSE stream nor session teardown applies here.
        return request.make_response(
            "", headers=cors_headers(self._origin()) + [("Allow", "POST, OPTIONS")],
            status=405)

    @http.route(["/mcp", "/mcp/v1"], type="http", auth="none",
                methods=["POST"], csrf=False, save_session=False)
    def mcp_post(self, **kw):
        # DNS-rebinding guard: a hostile page must not be able to drive this
        # endpoint from a victim's browser.
        origin = self._origin()
        if origin and not _origin_allowed(request.env, origin):
            return self._json({"error": "forbidden_origin"}, status=403)
        if not param_enabled(request.env, "enabled", True):
            return self._json({"error": "mcp_disabled"}, status=503)

        auth = self._authenticate()
        if not auth:
            return self._unauthorized()
        scope, audit_ctx = auth

        try:
            message = json.loads(request.httprequest.get_data() or b"{}")
        except ValueError:
            return self._rpc_error(None, PARSE_ERROR, "Parse error")
        if not isinstance(message, dict):
            return self._rpc_error(None, INVALID_REQUEST, "Invalid Request")

        method = message.get("method")
        # Notifications / responses carry no id -> acknowledge with 202. Header
        # requirements are not defined for notification POSTs.
        if "id" not in message:
            return request.make_response(
                "", headers=cors_headers(origin), status=202)

        mid = message.get("id")
        params = message.get("params") or {}
        # JSON-RPC permits positional params; MCP does not. Reject rather than
        # let a list reach the .get() calls below as a 500.
        if not isinstance(params, dict):
            return self._rpc_error(mid, INVALID_PARAMS, "params must be an object")
        modern = self._is_modern(message)

        if modern:
            invalid = self._validate_modern(mid, method, params)
            if invalid:
                return invalid
        elif method != "initialize" and not self._legacy_version_ok():
            return self._unsupported_version(
                mid, request.httprequest.headers.get("MCP-Protocol-Version"))

        try:
            result = self._dispatch(method, params, scope, audit_ctx, modern)
        except MCPInsufficientScope as exc:
            return self._insufficient_scope(mid, exc.required, exc.description)
        except _RpcError as exc:
            return self._rpc_error(mid, exc.code, exc.message, status=exc.status)

        # 2026-07-28 makes `resultType` mandatory on every result: it is the
        # discriminator that tells a client whether this is the final answer or
        # an input_required round-trip. Earlier eras never defined the field and
        # their clients treat an absent one as "complete", so only modern
        # responses carry it.
        if modern and isinstance(result, dict):
            result.setdefault("resultType", "complete")

        extra = []
        # Legacy clients track a session id; modern ones must never be given
        # one (protocol-level sessions were removed in 2026-07-28).
        if not modern and method == "initialize":
            extra.append(("Mcp-Session-Id", secrets.token_urlsafe(24)))
        return self._json(
            {"jsonrpc": "2.0", "id": mid, "result": result}, extra_headers=extra)

    # ------------------------------------------------------------ era handling
    def _is_modern(self, message):
        """Decide which era this request speaks.

        An `initialize` call is legacy by definition. Otherwise the presence of
        modern per-request metadata (in `_meta` or the mirrored header) marks a
        modern request.
        """
        if message.get("method") == "initialize":
            return False
        meta = (message.get("params") or {}).get("_meta")
        meta = meta if isinstance(meta, dict) else {}
        if meta.get(META_VERSION):
            return True
        header = request.httprequest.headers.get("MCP-Protocol-Version")
        return header in MODERN_PROTOCOL_VERSIONS

    def _legacy_version_ok(self):
        """A legacy client MAY omit the header entirely (pre-2025-06-18)."""
        header = request.httprequest.headers.get("MCP-Protocol-Version")
        return header is None or header in SUPPORTED_PROTOCOL_VERSIONS

    def _validate_modern(self, mid, method, params):
        """Header/body agreement checks required of modern requests.

        Returning a response means the request is rejected; None means it is
        well-formed. The point of these checks is that an intermediary routing
        on headers and a server executing on the body must never disagree.
        """
        headers = request.httprequest.headers
        header_version = headers.get("MCP-Protocol-Version")
        meta = params.get("_meta")
        meta_version = meta.get(META_VERSION) if isinstance(meta, dict) else None

        if not header_version:
            return self._rpc_error(
                mid, HEADER_MISMATCH,
                "Missing required header: MCP-Protocol-Version", status=400)
        if meta_version and meta_version != header_version:
            return self._rpc_error(
                mid, HEADER_MISMATCH,
                "MCP-Protocol-Version header does not match "
                "_meta['%s']" % META_VERSION, status=400)
        if header_version not in SUPPORTED_PROTOCOL_VERSIONS:
            return self._unsupported_version(mid, header_version)

        header_method = headers.get("Mcp-Method")
        if not header_method:
            return self._rpc_error(
                mid, HEADER_MISMATCH, "Missing required header: Mcp-Method",
                status=400)
        if header_method != method:
            return self._rpc_error(
                mid, HEADER_MISMATCH,
                "Mcp-Method header value '%s' does not match body value '%s'"
                % (header_method, method), status=400)

        source = NAME_HEADER_SOURCES.get(method)
        if source:
            body_value = params.get(source)
            header_name = decode_header_value(headers.get("Mcp-Name"))
            if header_name is None:
                return self._rpc_error(
                    mid, HEADER_MISMATCH, "Missing required header: Mcp-Name",
                    status=400)
            if body_value is not None and header_name != body_value:
                return self._rpc_error(
                    mid, HEADER_MISMATCH,
                    "Mcp-Name header value '%s' does not match body value '%s'"
                    % (header_name, body_value), status=400)
        return None

    def _unsupported_version(self, mid, requested):
        return self._json({
            "jsonrpc": "2.0", "id": mid,
            "error": {
                "code": UNSUPPORTED_PROTOCOL_VERSION,
                "message": "Unsupported protocol version",
                "data": {"supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                         "requested": requested},
            },
        }, status=400)

    # --------------------------------------------------------------- dispatcher
    def _dispatch(self, method, params, scope, audit_ctx, modern):
        engine = request.env["mcp.engine"]
        if method == "server/discover":
            return self._discover()
        if method == "initialize":
            # Reaching here always means the legacy era: an initialize call is
            # what *selects* legacy semantics, so _is_modern never flags it.
            return {
                "protocolVersion": self._negotiate_legacy(params),
                "serverInfo": SERVER_INFO,
                "capabilities": {"tools": {"listChanged": False},
                                 "prompts": {}, "resources": {}},
                "instructions": SERVER_INSTRUCTIONS,
            }
        if method in ("ping", "notifications/initialized"):
            return {}
        if method == "tools/list":
            return {"tools": engine.list_tools(scope)}
        if method == "tools/call":
            return engine.call_tool(
                scope, params.get("name"), params.get("arguments") or {}, audit_ctx)
        if method == "prompts/list":
            return {"prompts": engine.list_prompts(scope)}
        if method == "prompts/get":
            return engine.get_prompt(scope, params.get("name"), params.get("arguments") or {})
        if method == "resources/list":
            return {"resources": engine.list_resources(scope)}
        if method == "resources/read":
            return engine.read_resource(scope, params.get("uri"))
        # Modern transport distinguishes an unknown method with 404 so a client
        # can tell it apart from a legacy server that has no modern endpoint.
        raise _RpcError(METHOD_NOT_FOUND, "Method not found: %s" % method,
                        status=404 if modern else 200)

    def _discover(self):
        """server/discover - mandatory in 2026-07-28."""
        return {
            "resultType": "complete",
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
            "_meta": {META_PREFIX + "serverInfo": SERVER_INFO},
            "instructions": SERVER_INSTRUCTIONS,
            "ttlMs": 3600000,
            "cacheScope": "public",
        }

    def _negotiate_legacy(self, params):
        want = (params or {}).get("protocolVersion")
        return want if want in LEGACY_PROTOCOL_VERSIONS else LATEST_LEGACY_VERSION

    def _rpc_error(self, mid, code, message, status=200):
        return self._json(
            {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}},
            status=status)


class _RpcError(Exception):
    def __init__(self, code, message, status=200):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
