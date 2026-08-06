# -*- coding: utf-8 -*-
"""OAuth 2.1 endpoints - the self-contained Authorization Server.

Implements exactly what the MCP authorization spec requires:
* RFC 9728  Protected Resource Metadata .... /.well-known/oauth-protected-resource
* RFC 8414  Authorization Server Metadata ... /.well-known/oauth-authorization-server
* CIMD      Client ID Metadata Documents .... client_id as an https URL (preferred)
* RFC 7591  Dynamic Client Registration ..... POST /oauth/register (deprecated)
* RFC 7636  Authorization Code + PKCE (S256)  GET/POST /oauth/authorize, POST /oauth/token
* RFC 8707  Resource Indicators (audience) .. `resource` on code + token
* RFC 9207  Issuer Identification ........... `iss` on every authorization response
* RFC 7009  Token Revocation ................ POST /oauth/revoke

The user authenticates with their normal Odoo login (the /oauth/authorize route
is auth="user"), so we never handle passwords ourselves and SSO/2FA just work.

Spec: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
"""
import json
import logging
import time
from urllib.parse import urlencode, urlparse

from werkzeug.utils import redirect as wz_redirect

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request

from ..models.tools_crypto import hash_secret, new_secret

_logger = logging.getLogger(__name__)

# The two scopes this resource understands. Reading is always available;
# writing is gated on the governance scope, on the resource owner ticking the
# consent box, and on the per-model permission matrix underneath. Both are
# advertised so a client can request write up front rather than discovering it
# only through a step-up round-trip.
SCOPE_READ = "odoo:read"
SCOPE_WRITE = "odoo:write"
SCOPES_SUPPORTED = [SCOPE_READ, SCOPE_WRITE]

# Early builds shipped dot-separated names; keep accepting them so a client
# that cached the old metadata does not break on upgrade.
_LEGACY_SCOPE_ALIASES = {
    "odoo": SCOPE_READ,
    "odoo.read": SCOPE_READ,
    "odoo.write": SCOPE_WRITE,
}

# The MCP endpoint answers on both paths; either is a valid audience for us.
MCP_ENDPOINT_PATHS = ("/mcp", "/mcp/v1")

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


# --------------------------------------------------------------------- helpers
def _request_origin():
    return request.httprequest.host_url.rstrip("/")


def base_url():
    """The public origin clients actually reach.

    ``host_url`` reports the scheme the WSGI layer saw, which is plain http
    behind a TLS-terminating proxy unless Odoo runs with ``proxy_mode``. An
    authorization server that advertises http endpoints on an https deployment
    violates OAuth 2.1 §1.5 and is rejected by strict clients, so the
    configured public address wins whenever it names this same host - that
    fixes the scheme without ever letting a stale ``web.base.url`` redirect
    metadata at a different server.
    """
    origin = _request_origin()
    configured = request.env["ir.config_parameter"].sudo().get_param(
        "web.base.url", "").strip().rstrip("/")
    if configured.startswith(("http://", "https://")):
        try:
            if urlparse(configured).netloc == urlparse(origin).netloc:
                return configured
        except ValueError:
            pass
    return origin


def canonical_resource():
    """RFC 8707 canonical URI of this MCP server - the audience we advertise."""
    return base_url() + "/mcp"


def accepted_resources():
    """Every URI that identifies *this* MCP server.

    The endpoint is reachable at more than one path, and a client is told to
    use "the most specific URI it can", so a token may legitimately carry
    either. Both name the same resource, so both are accepted - and nothing
    else is (RFC 8707 audience binding).

    The request origin is accepted alongside the canonical base so that tokens
    minted before this server learned its own scheme keep validating; both
    spellings are the same host, so this narrows nothing.
    """
    bases = {base_url(), _request_origin()}
    return {base + path for base in bases for path in MCP_ENDPOINT_PATHS}


def normalize_scopes(raw, default=None):
    """Parse a space-separated scope string into a validated, ordered list.

    ``default`` covers the RFC 6749 §3.3 rule that an authorization server
    receiving no scope MUST apply a pre-defined default. It stays read-only
    unless a caller deliberately widens it, so an empty or unparseable scope
    can never fall open.
    """
    out = []
    for token in (raw or "").split():
        mapped = _LEGACY_SCOPE_ALIASES.get(token, token)
        if mapped in SCOPES_SUPPORTED and mapped not in out:
            out.append(mapped)
    return out or list(default or [SCOPE_READ])


def writes_enabled(env):
    """True when this deployment has any scope that permits writes at all.

    Used to decide what to advertise: offering ``odoo:write`` on a server whose
    every governance scope is read-only would send clients through a step-up
    flow that can never succeed.
    """
    return bool(env["mcp.scope"].sudo().search_count(
        [("active", "=", True), ("read_only", "=", False)]))


def grantable_scopes(governance):
    """The most a given governance scope is allowed to hand to a token."""
    if governance and not governance.read_only:
        return [SCOPE_READ, SCOPE_WRITE]
    return [SCOPE_READ]


def param_enabled(env, suffix, default=True):
    val = env["ir.config_parameter"].sudo().get_param(
        "mcp_governance_suite.%s" % suffix, "1" if default else "0")
    return str(val).lower() in ("1", "true", "t", "yes")


def _cors():
    return [
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Authorization, Content-Type"),
        ("Access-Control-Max-Age", "86400"),
    ]


def _json(data, status=200, extra=None):
    return request.make_json_response(data, status=status, headers=_cors() + (extra or []))


def _oauth_error(error, description=None, status=400):
    body = {"error": error}
    if description:
        body["error_description"] = description
    return _json(body, status=status,
                 extra=[("Cache-Control", "no-store"), ("Pragma", "no-cache")])


def is_valid_redirect(uri):
    """OAuth 2.1 §1.5: redirect URIs must be https, or http on loopback.

    Parsed rather than prefix-matched on purpose. ``startswith("http://localhost")``
    also accepts ``http://localhost.attacker.example``, which - with open client
    registration - hands an attacker a working callback for someone else's
    authorization code.
    """
    if not uri:
        return False
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.fragment:
        return False  # RFC 6749 §3.1.2: no fragment component
    if parsed.scheme == "https":
        return bool(parsed.hostname)
    if parsed.scheme == "http":
        return parsed.hostname in LOOPBACK_HOSTS
    return False


def _form_or_json(kw):
    """Token/registration bodies arrive as form-encoded or JSON; accept both."""
    if kw:
        return dict(kw)
    try:
        return json.loads(request.httprequest.get_data() or b"{}")
    except ValueError:
        return {}


def _render_page(template, values):
    """Render a standalone HTML page, prepending the doctype at the render layer
    (an inline <!DOCTYPE> is invalid inside an XML template)."""
    html = request.env["ir.qweb"]._render(template, values)
    return request.make_response(
        "<!DOCTYPE html>\n" + str(html),
        headers=[("Content-Type", "text/html; charset=utf-8")])


class MCPOAuthController(http.Controller):

    # =================================================== discovery (RFC 9728/8414)
    @http.route(["/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-protected-resource/mcp",
                 "/.well-known/oauth-protected-resource/mcp/v1"],
                type="http", auth="none", methods=["GET", "OPTIONS"],
                csrf=False, save_session=False)
    def protected_resource_metadata(self, **kw):
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors(), status=204)
        # RFC 9728 §3.3: a client that built this URL from its resource
        # identifier validates that `resource` comes back matching. Echo the
        # identifier that belongs to the path we were asked about, or the
        # canonical one at the bare well-known location.
        path = request.httprequest.path
        suffix = path[len("/.well-known/oauth-protected-resource"):]
        resource = (base_url() + suffix) if suffix in MCP_ENDPOINT_PATHS \
            else canonical_resource()
        return _json({
            "resource": resource,
            "authorization_servers": [base_url()],
            "bearer_methods_supported": ["header"],
            # Deliberately excludes offline_access: refresh tokens are not a
            # resource requirement (MCP authorization, Refresh Tokens).
            "scopes_supported": SCOPES_SUPPORTED,
            "resource_documentation": base_url() + "/mcp/health",
        })

    @http.route(["/.well-known/oauth-authorization-server",
                 "/.well-known/oauth-authorization-server/mcp"],
                type="http", auth="none", methods=["GET", "OPTIONS"],
                csrf=False, save_session=False)
    def authorization_server_metadata(self, **kw):
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors(), status=204)
        base = base_url()
        body = {
            # MUST string-match the `iss` we emit on authorization responses.
            "issuer": base,
            "authorization_endpoint": base + "/oauth/authorize",
            "token_endpoint": base + "/oauth/token",
            "revocation_endpoint": base + "/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            # offline_access belongs here (a client MAY request it) but not in
            # the protected-resource metadata above.
            "scopes_supported": SCOPES_SUPPORTED + ["offline_access"],
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": True,
        }
        if param_enabled(request.env, "dynamic_registration", True):
            body["registration_endpoint"] = base + "/oauth/register"
        return _json(body)

    # ================================================== client resolution (CIMD)
    def _resolve_client(self, client_id):
        """Find the client, fetching its metadata document when the id is a URL.

        Returns (client, error_response). CIMD failures are reported as
        invalid_client rather than raising, so the caller can answer in the
        right shape for its endpoint.
        """
        if not client_id:
            return None, _oauth_error("invalid_request", "client_id is required.")
        try:
            client = request.env["mcp.oauth.client"].sudo().resolve(client_id)
        except UserError as exc:
            _logger.info("CIMD resolution failed for %s: %s", client_id, exc)
            return None, _oauth_error("invalid_client", str(exc), status=401)
        if not client:
            return None, _oauth_error("invalid_client", "Unknown client.", status=401)
        return client, None

    # =================================================== registration (RFC 7591)
    @http.route("/oauth/register", type="http", auth="none",
                methods=["POST", "OPTIONS"], csrf=False, save_session=False)
    def register(self, **kw):
        """Dynamic Client Registration.

        Deprecated upstream in favour of Client ID Metadata Documents and kept
        only for clients that cannot host a metadata document.
        """
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors(), status=204)
        if not param_enabled(request.env, "dynamic_registration", True):
            return _oauth_error("access_denied",
                                "Dynamic registration is disabled.", status=403)
        meta = _form_or_json(kw)
        redirect_uris = meta.get("redirect_uris") or []
        if isinstance(redirect_uris, str):
            redirect_uris = [redirect_uris]
        if not redirect_uris or not all(is_valid_redirect(u) for u in redirect_uris):
            return _oauth_error("invalid_redirect_uri",
                                "redirect_uris must be https, or http on loopback.")
        auth_method = meta.get("token_endpoint_auth_method") or "none"
        if auth_method not in ("none", "client_secret_post"):
            auth_method = "none"

        client_id = new_secret(prefix="mcpc-", nbytes=18)
        secret = None
        vals = {
            "name": (meta.get("client_name") or "MCP Client")[:120],
            "client_id": client_id,
            "registration_type": "dcr",
            "token_endpoint_auth_method": auth_method,
            "redirect_uris": "\n".join(redirect_uris),
            "grant_types": " ".join(meta.get("grant_types") or
                                    ["authorization_code", "refresh_token"]),
            # A client that declares no scope at registration is telling us
            # nothing about its intent, not asking to be read-only. Registering
            # it as read-only would pin it there for good, because clients
            # replay their registered scope on every authorization request.
            "scope": " ".join(normalize_scopes(
                meta.get("scope"), default=SCOPES_SUPPORTED)),
        }
        if auth_method == "client_secret_post":
            secret = new_secret(prefix="mcps-", nbytes=24)
            vals["client_secret_hash"] = hash_secret(secret)
        request.env["mcp.oauth.client"].sudo().create(vals)

        body = {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": auth_method,
            "grant_types": vals["grant_types"].split(),
            "response_types": ["code"],
            "client_name": vals["name"],
            "scope": vals["scope"],
        }
        if secret:
            body["client_secret"] = secret
            body["client_secret_expires_at"] = 0  # never expires
        return _json(body, status=201,
                     extra=[("Cache-Control", "no-store"), ("Pragma", "no-cache")])

    # ================================================= authorization (RFC 7636)
    @http.route("/oauth/authorize", type="http", auth="user",
                methods=["GET"], csrf=False, save_session=True)
    def authorize(self, **kw):
        if not param_enabled(request.env, "oauth_enabled", True):
            return _render_page("mcp_governance_suite.oauth_error",
                                {"message": "OAuth is disabled on this server."})
        client, error = self._resolve_client(kw.get("client_id"))
        if error:
            # Never redirect on an unresolved client - we have no trustworthy
            # redirect target yet, so report on-server.
            return _render_page("mcp_governance_suite.oauth_error", {
                "message": "Unknown client, or its metadata document could not "
                           "be validated."})
        redirect_uri = kw.get("redirect_uri")
        if not client.validate_redirect_uri(redirect_uri):
            return _render_page("mcp_governance_suite.oauth_error", {
                "message": "The redirect URI is not registered for this client."})

        if kw.get("response_type") != "code":
            return self._authorize_redirect_error(
                redirect_uri, "unsupported_response_type", kw.get("state"))
        if kw.get("code_challenge_method", "S256") != "S256" or not kw.get("code_challenge"):
            return self._authorize_redirect_error(
                redirect_uri, "invalid_request", kw.get("state"),
                "PKCE with S256 is required.")
        resource, res_error = self._resolve_target(kw.get("resource"))
        if res_error:
            return self._authorize_redirect_error(
                redirect_uri, "invalid_target", kw.get("state"), res_error)

        governance = request.env.user.mcp_effective_scope()
        if not governance:
            return self._authorize_redirect_error(
                redirect_uri, "server_error", kw.get("state"),
                "No MCP governance scope is configured on this server.")
        grantable = grantable_scopes(governance)
        # RFC 6749 §3.3: a client that asks for nothing gets our default, which
        # is everything this governance scope permits. Anything it does ask for
        # is still capped by the scope - a read-only scope grants no write.
        requested = normalize_scopes(kw.get("scope"), default=grantable)
        granted = [s for s in requested if s in grantable] or [SCOPE_READ]
        # Clients following the MCP scope-selection strategy pin the scope set
        # from our 401 challenge and never widen it on their own. So the
        # resource owner gets the say here: offer write whenever the governance
        # scope allows it, even when the client only asked to read.
        offer_write = SCOPE_WRITE in grantable
        if offer_write and SCOPE_WRITE not in granted:
            granted = granted + [SCOPE_WRITE]
        # The ✓ list states what is granted outright; write is the one grant
        # the user decides here, so it is rendered as the checkbox instead.
        baseline = [s for s in granted if s != SCOPE_WRITE]
        return _render_page("mcp_governance_suite.oauth_consent", {
            "client": client,
            "user": request.env.user,
            "scope": governance,
            "granted_scopes": granted,
            "offer_write": offer_write,
            "scope_labels": self._scope_labels(baseline),
            "access_ttl_hours": max(1, round(int(
                request.env["ir.config_parameter"].sudo().get_param(
                    "mcp_governance_suite.access_token_ttl", 3600)) / 3600)),
            "params": {
                "client_id": client.client_id,
                "redirect_uri": redirect_uri,
                "state": kw.get("state") or "",
                "code_challenge": kw.get("code_challenge"),
                "code_challenge_method": "S256",
                "scope": " ".join(granted),
                "resource": resource,
            },
            "csrf_token": request.csrf_token(),
        })

    def _scope_labels(self, granted):
        """Plain-language consent lines - never show raw scope tokens alone."""
        labels = {
            SCOPE_READ: "Read the Odoo records this connection's scope allows",
            SCOPE_WRITE: "Create and update records you already have rights to",
        }
        return [labels[s] for s in granted if s in labels]

    def _resolve_target(self, requested):
        """Validate the RFC 8707 `resource` parameter against what we serve."""
        if not requested:
            return canonical_resource(), None
        normalized = requested.rstrip("/")
        if normalized not in accepted_resources():
            return None, "Unknown target resource for this server."
        return normalized, None

    @http.route("/oauth/authorize/decision", type="http", auth="user",
                methods=["POST"], csrf=True, save_session=True)
    def authorize_decision(self, **kw):
        client, error = self._resolve_client(kw.get("client_id"))
        if error or not client.validate_redirect_uri(kw.get("redirect_uri")):
            return _render_page("mcp_governance_suite.oauth_error", {
                "message": "Unknown client or unregistered redirect URI."})
        redirect_uri = kw.get("redirect_uri")
        state = kw.get("state") or ""
        if kw.get("decision") != "allow":
            return self._authorize_redirect_error(redirect_uri, "access_denied", state)

        resource, res_error = self._resolve_target(kw.get("resource"))
        if res_error:
            return self._authorize_redirect_error(
                redirect_uri, "invalid_target", state, res_error)

        governance = request.env.user.mcp_effective_scope()
        if not governance:
            return self._authorize_redirect_error(
                redirect_uri, "server_error", state,
                "No MCP governance scope is configured on this server.")
        grantable = grantable_scopes(governance)
        granted = [s for s in normalize_scopes(kw.get("scope"), default=grantable)
                   if s in grantable]
        # The checkbox is authoritative for write: an unticked box posts
        # nothing, so a user who declined it gets a read-only token even though
        # the form carried odoo:write. A read-only governance scope has already
        # dropped write from `grantable`, so there is nothing left to tick.
        if not kw.get("grant_write"):
            granted = [s for s in granted if s != SCOPE_WRITE]
        granted = granted or [SCOPE_READ]
        code = new_secret(prefix="mcpac-", nbytes=24)
        request.env["mcp.oauth.authcode"].issue({
            "code_hash": hash_secret(code),
            "client_id": client.client_id,
            "user_id": request.env.uid,
            "scope_id": governance.id,
            "redirect_uri": redirect_uri,
            "code_challenge": kw.get("code_challenge"),
            "code_challenge_method": "S256",
            "resource": resource,
            "scope": " ".join(granted),
        })
        query = {"code": code}
        if state:
            query["state"] = state
        return self._redirect_with(redirect_uri, query)

    def _authorize_redirect_error(self, redirect_uri, error, state, description=None):
        query = {"error": error}
        if description:
            query["error_description"] = description
        if state:
            query["state"] = state
        return self._redirect_with(redirect_uri, query)

    def _redirect_with(self, redirect_uri, query):
        # RFC 9207: `iss` goes on every authorization response, successes and
        # errors alike, so the client can detect a mix-up attack.
        query = dict(query, iss=base_url())
        sep = "&" if "?" in redirect_uri else "?"
        return wz_redirect(redirect_uri + sep + urlencode(query), code=302)

    # ========================================================= token (RFC 7636)
    @http.route("/oauth/token", type="http", auth="none",
                methods=["POST", "OPTIONS"], csrf=False, save_session=False)
    def token(self, **kw):
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors(), status=204)
        data = _form_or_json(kw)
        grant = data.get("grant_type")
        if grant == "authorization_code":
            return self._grant_authorization_code(data)
        if grant == "refresh_token":
            return self._grant_refresh_token(data)
        return _oauth_error("unsupported_grant_type")

    def _authenticate_client(self, data):
        client, error = self._resolve_client(data.get("client_id"))
        if error:
            return None, error
        if not client.check_secret(data.get("client_secret")):
            return None, _oauth_error("invalid_client", status=401)
        return client, None

    def _grant_authorization_code(self, data):
        client, error = self._authenticate_client(data)
        if error:
            return error
        authcode = request.env["mcp.oauth.authcode"].sudo().search(
            [("code_hash", "=", hash_secret(data.get("code") or ""))], limit=1)
        if not authcode:
            return _oauth_error("invalid_grant", "Authorization code invalid or expired.")
        # Claim the code atomically. Losing this race means it was already
        # redeemed, which OAuth 2.1 §4.1.3.4 treats as a compromise: revoke
        # everything that code produced.
        if not authcode.consume():
            authcode.revoke_issued_tokens()
            return _oauth_error("invalid_grant", "Authorization code already used.")
        if authcode.is_expired():
            return _oauth_error("invalid_grant", "Authorization code invalid or expired.")
        if authcode.client_id != client.client_id or \
                authcode.redirect_uri != data.get("redirect_uri"):
            return _oauth_error("invalid_grant", "Client or redirect_uri mismatch.")
        if not authcode.verify_pkce(data.get("code_verifier") or ""):
            return _oauth_error("invalid_grant", "PKCE verification failed.")
        # A token request MAY narrow the audience, but never widen it.
        resource, res_error = self._resolve_target(
            data.get("resource") or authcode.resource)
        if res_error:
            return _oauth_error("invalid_target", res_error)
        return self._issue_tokens(client, authcode.user_id, authcode.scope_id,
                                  resource, authcode.scope, authcode=authcode)

    def _grant_refresh_token(self, data):
        client, error = self._authenticate_client(data)
        if error:
            return error
        old = request.env["mcp.oauth.token"].sudo().search(
            [("refresh_token_hash", "=", hash_secret(data.get("refresh_token") or ""))],
            limit=1)
        if not old or not old.is_refresh_valid() or old.client_id != client.client_id:
            return _oauth_error("invalid_grant", "Refresh token invalid or expired.")
        # OAuth 2.1: rotate refresh tokens for public clients.
        old.revoked = True
        return self._issue_tokens(client, old.user_id, old.scope_id, old.resource,
                                  old.scope, authcode=old.authcode_id)

    def _issue_tokens(self, client, user, scope, resource, oauth_scope, authcode=None):
        access_token = new_secret(prefix="mcpat-", nbytes=32)
        refresh_token = new_secret(prefix="mcprt-", nbytes=32)
        granted = normalize_scopes(oauth_scope)
        token = request.env["mcp.oauth.token"].issue(access_token, refresh_token, {
            "client_id": client.client_id,
            "client_name": client.name,
            "authcode_id": authcode.id if authcode else False,
            "user_id": user.id,
            "scope_id": scope.id,
            "resource": resource,
            "scope": " ".join(granted),
        })
        return _json({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": token.access_ttl_seconds(),
            "refresh_token": refresh_token,
            "scope": " ".join(granted),
        }, extra=[("Cache-Control", "no-store"), ("Pragma", "no-cache")])

    # ===================================================== revocation (RFC 7009)
    @http.route("/oauth/revoke", type="http", auth="none",
                methods=["POST", "OPTIONS"], csrf=False, save_session=False)
    def revoke(self, **kw):
        """RFC 7009 §2.2: always answer 200, even for an unknown token.

        Telling a caller whether a token existed is an oracle; the RFC requires
        the same response either way.
        """
        if request.httprequest.method == "OPTIONS":
            return request.make_response("", headers=_cors(), status=204)
        data = _form_or_json(kw)
        client, error = self._authenticate_client(data)
        if error:
            return error
        secret = data.get("token") or ""
        if secret:
            digest = hash_secret(secret)
            hint = data.get("token_type_hint")
            domain = ["|", ("access_token_hash", "=", digest),
                      ("refresh_token_hash", "=", digest)]
            if hint == "access_token":
                domain = [("access_token_hash", "=", digest)]
            elif hint == "refresh_token":
                domain = [("refresh_token_hash", "=", digest)]
            # Only ever revoke a token that belongs to the caller.
            domain.append(("client_id", "=", client.client_id))
            request.env["mcp.oauth.token"].sudo().search(domain).write({"revoked": True})
        return _json({}, extra=[("Cache-Control", "no-store")])
