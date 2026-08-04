# -*- coding: utf-8 -*-
"""OAuth 2.1 authorization server for the MCP connector.

The Odoo instance is *both* the MCP Resource Server and its Authorization
Server (self-contained: no external IdP to deploy). This module implements the
subset of OAuth 2.1 that the MCP specification mandates:

* Client ID Metadata Documents ...... draft-ietf-oauth-client-id-metadata-document-00
                                      -> mcp.oauth.client (registration_type='cimd')
* Dynamic Client Registration ....... RFC 7591  -> deprecated, kept for compatibility
* Authorization Code + PKCE (S256) .. RFC 7636  -> mcp.oauth.authcode
* Refresh-token rotation ............ OAuth 2.1 -> mcp.oauth.token
* Resource Indicators (audience) .... RFC 8707  -> `resource` on code + token

MCP authorization spec (2026-07-28):
https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization

Design notes
------------
* Only digests are stored (see tools_crypto). The controller hands us the
  plaintext, we hash and compare.
* Every code/token carries the governance `scope_id` resolved at consent time,
  so the engine never has to trust the client about what it may touch.
* TTLs are configuration, not constants (ir.config_parameter), because a
  finance customer and a demo tenant want very different token lifetimes.
* CIMD is the happy path: the client's `client_id` *is* an HTTPS URL serving
  its own metadata, so the user never copies a client id or secret. DCR is
  deprecated upstream and retained only for clients that cannot do CIMD.
"""
import ipaddress
import json
import logging
import socket
from datetime import timedelta
from urllib.parse import urlparse

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .tools_crypto import (
    constant_time_equals,
    hash_secret,
    pkce_s256_challenge,
)

_logger = logging.getLogger(__name__)

# ir.config_parameter keys (seconds) -- overridable per database.
PARAM_ACCESS_TTL = "mcp_governance_suite.access_token_ttl"
PARAM_REFRESH_TTL = "mcp_governance_suite.refresh_token_ttl"
PARAM_CODE_TTL = "mcp_governance_suite.authcode_ttl"
PARAM_CIMD_TTL = "mcp_governance_suite.cimd_cache_ttl"

DEFAULT_ACCESS_TTL = 3600          # 1 hour  - short-lived per OAuth 2.1 §7.1
DEFAULT_REFRESH_TTL = 60 * 60 * 24 * 30  # 30 days
DEFAULT_CODE_TTL = 300             # 5 minutes - single use
DEFAULT_CIMD_TTL = 3600            # re-fetch client metadata hourly

# CIMD fetch hardening.
CIMD_TIMEOUT = 5                   # seconds
CIMD_MAX_BYTES = 64 * 1024         # a metadata document is a few hundred bytes


def _ttl(env, param, default):
    return int(env["ir.config_parameter"].sudo().get_param(param, default))


# --------------------------------------------------------------- CIMD fetching
def is_cimd_client_id(client_id):
    """True when a client_id is a Client ID Metadata Document URL.

    Per the CIMD draft the identifier MUST use the https scheme and MUST carry
    a path component, which is what distinguishes it from an opaque id.
    """
    if not client_id or not client_id.startswith("https://"):
        return False
    parsed = urlparse(client_id)
    return bool(parsed.netloc) and parsed.path not in ("", "/") and not parsed.fragment


def _assert_public_host(url):
    """Refuse to fetch anything that resolves to a non-public address (SSRF).

    Blocks loopback, private, link-local, multicast and otherwise reserved
    ranges so a hostile ``client_id`` cannot make Odoo probe its own network.
    Note the residual DNS-rebinding window: we validate the addresses the name
    resolves to now, and ``requests`` resolves again when it connects. Keeping
    the fetch to a 5s timeout, a byte cap and no redirects bounds the exposure.
    """
    host = urlparse(url).hostname
    if not host:
        raise UserError(_("Client metadata URL has no host."))
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UserError(_("Cannot resolve client metadata host: %s") % host) from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global or addr.is_multicast:
            raise UserError(
                _("Client metadata host %(host)s resolves to a non-public "
                  "address (%(addr)s); refusing to fetch.",
                  host=host, addr=addr))


def fetch_cimd_document(url):
    """Fetch and structurally validate a Client ID Metadata Document.

    Returns the parsed dict. Raises UserError on anything suspicious - the
    caller turns that into an OAuth ``invalid_client`` error.
    """
    if not is_cimd_client_id(url):
        raise UserError(_("client_id must be an https URL with a path component."))
    _assert_public_host(url)
    try:
        resp = requests.get(
            url, timeout=CIMD_TIMEOUT, stream=True,
            # A redirect could bounce us to an internal address after the
            # host check, so refuse them outright.
            allow_redirects=False,
            headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise UserError(_("Could not fetch client metadata: %s") % exc) from exc
    if resp.status_code != 200:
        raise UserError(
            _("Client metadata URL returned HTTP %s.") % resp.status_code)
    raw = resp.raw.read(CIMD_MAX_BYTES + 1, decode_content=True)
    if len(raw) > CIMD_MAX_BYTES:
        raise UserError(_("Client metadata document is too large."))
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UserError(_("Client metadata is not valid JSON.")) from exc
    if not isinstance(doc, dict):
        raise UserError(_("Client metadata must be a JSON object."))

    # The draft requires these three, and requires client_id to match the URL
    # exactly - that equality is what stops one client claiming another's id.
    for required in ("client_id", "client_name", "redirect_uris"):
        if not doc.get(required):
            raise UserError(
                _("Client metadata is missing required field '%s'.") % required)
    if doc["client_id"] != url:
        raise UserError(
            _("Client metadata client_id does not match its URL."))
    if not isinstance(doc["redirect_uris"], list):
        raise UserError(_("Client metadata redirect_uris must be a list."))
    return doc


class MCPOAuthClient(models.Model):
    """A registered MCP client (Claude, ChatGPT, Cursor, ...).

    Populated automatically from a Client ID Metadata Document (the happy
    path), through RFC 7591 dynamic registration (deprecated), or by hand for
    clients that can do neither.
    """
    _name = "mcp.oauth.client"
    _description = "MCP OAuth Client"
    _order = "create_date desc"

    name = fields.Char(string="Client Name", required=True, default="MCP Client")
    client_id = fields.Char(required=True, index=True, copy=False, readonly=True)
    registration_type = fields.Selection(
        [("cimd", "Client ID Metadata Document"),
         ("dcr", "Dynamic Registration (deprecated)"),
         ("manual", "Manually registered")],
        default="manual", required=True, readonly=True,
        help="How this client obtained its client_id. CIMD is the mechanism "
             "the MCP specification recommends; dynamic registration is "
             "deprecated upstream and kept for backwards compatibility.")
    # Public clients (PKCE, no secret) store an empty hash; confidential clients
    # store only the digest of their secret.
    client_secret_hash = fields.Char(readonly=True, copy=False)
    token_endpoint_auth_method = fields.Selection(
        [("none", "none (public + PKCE)"),
         ("client_secret_post", "client_secret_post (confidential)")],
        default="none", required=True,
        help="Public clients authenticate with PKCE only, as recommended by "
             "OAuth 2.1 for user-facing apps such as Claude Desktop.")
    redirect_uris = fields.Text(
        required=True,
        help="Newline-separated list of exact redirect URIs. Only loopback or "
             "HTTPS URIs are accepted (OAuth 2.1 §1.5).")
    grant_types = fields.Char(default="authorization_code refresh_token")
    scope = fields.Char(help="Space-separated scopes the client requested.")
    active = fields.Boolean(default=True)
    token_count = fields.Integer(compute="_compute_token_count")

    # -- CIMD bookkeeping ---------------------------------------------------
    client_uri = fields.Char(readonly=True, help="Client home page, from its metadata.")
    logo_uri = fields.Char(readonly=True)
    jwks_uri = fields.Char(
        readonly=True,
        help="Advertised by the client for private_key_jwt authentication. "
             "Stored for forward compatibility; not yet used to verify "
             "assertions.")
    metadata_json = fields.Text(readonly=True, help="Last fetched CIMD document.")
    metadata_fetched_at = fields.Datetime(readonly=True)

    _client_id_uniq = models.Constraint(
        "UNIQUE (client_id)", "client_id must be unique.")

    def _compute_token_count(self):
        data = self.env["mcp.oauth.token"].sudo()._read_group(
            [("client_id", "in", self.mapped("client_id")),
             ("revoked", "=", False)],
            groupby=["client_id"], aggregates=["__count"])
        counts = {cid: n for cid, n in data}
        for rec in self:
            rec.token_count = counts.get(rec.client_id, 0)

    # ------------------------------------------------------------------ CIMD
    @api.model
    def resolve(self, client_id):
        """Return the client for ``client_id``, resolving CIMD on demand.

        A URL-shaped client_id is fetched (or refreshed from cache) and stored,
        so an unknown-but-valid MCP client can authorize without any admin
        action. Anything else must already be registered.
        """
        if not client_id:
            return self.browse()
        existing = self.sudo().search(
            [("client_id", "=", client_id), ("active", "=", True)], limit=1)
        if not is_cimd_client_id(client_id):
            return existing
        if existing and not existing._cimd_is_stale():
            return existing
        try:
            doc = fetch_cimd_document(client_id)
        except UserError:
            # A refresh failure must not lock out a client we already trust;
            # fall back to the cached document and re-try on the next request.
            if existing:
                _logger.warning(
                    "CIMD refresh failed for %s; using cached metadata", client_id)
                return existing
            raise
        return existing._apply_cimd(doc) if existing else self._create_from_cimd(doc)

    def _cimd_is_stale(self):
        self.ensure_one()
        if self.registration_type != "cimd" or not self.metadata_fetched_at:
            return False
        age = fields.Datetime.now() - self.metadata_fetched_at
        return age.total_seconds() > _ttl(self.env, PARAM_CIMD_TTL, DEFAULT_CIMD_TTL)

    @api.model
    def _cimd_vals(self, doc):
        """Map a metadata document onto our fields.

        Everything here is attacker-controllable: the client hosts its own
        document. It is safe to *store* and safe to *display as unverified*,
        but it must never grant anything - authority comes from the user's
        consent and from redirect_uri matching, not from these strings.
        """
        uris = [u for u in doc.get("redirect_uris") or [] if isinstance(u, str)]
        return {
            "name": (doc.get("client_name") or "MCP Client")[:120],
            "client_id": doc["client_id"],
            "registration_type": "cimd",
            "token_endpoint_auth_method": "none",
            "redirect_uris": "\n".join(uris),
            "grant_types": " ".join(
                doc.get("grant_types") or ["authorization_code", "refresh_token"]),
            "scope": doc.get("scope") or "",
            "client_uri": doc.get("client_uri"),
            "logo_uri": doc.get("logo_uri"),
            "jwks_uri": doc.get("jwks_uri"),
            "metadata_json": json.dumps(doc)[:20000],
            "metadata_fetched_at": fields.Datetime.now(),
        }

    @api.model
    def _create_from_cimd(self, doc):
        return self.sudo().create(self._cimd_vals(doc))

    def _apply_cimd(self, doc):
        """Refresh a cached client from a freshly fetched document.

        redirect_uris are re-validated on every fetch, so a client that drops
        a callback URL stops being able to use it here too.
        """
        self.ensure_one()
        self.sudo().write(self._cimd_vals(doc))
        return self

    # ------------------------------------------------------------ credentials
    def redirect_uri_list(self):
        self.ensure_one()
        return [u.strip() for u in (self.redirect_uris or "").splitlines() if u.strip()]

    def validate_redirect_uri(self, redirect_uri):
        """Exact-match validation (OAuth 2.1 §7.12 - no wildcard matching)."""
        self.ensure_one()
        return bool(redirect_uri) and redirect_uri in self.redirect_uri_list()

    def check_secret(self, secret):
        self.ensure_one()
        if self.token_endpoint_auth_method == "none":
            return True  # public client: PKCE is the proof, no secret expected
        return bool(self.client_secret_hash) and constant_time_equals(
            self.client_secret_hash, hash_secret(secret))

    def action_revoke_tokens(self):
        """Kill every live token for this client (incident response)."""
        self.env["mcp.oauth.token"].sudo().search(
            [("client_id", "in", self.mapped("client_id"))]).write({"revoked": True})


class MCPOAuthAuthCode(models.Model):
    """Short-lived, single-use authorization code bound to a PKCE challenge."""
    _name = "mcp.oauth.authcode"
    _description = "MCP OAuth Authorization Code"
    _rec_name = "client_id"

    code_hash = fields.Char(required=True, index=True, copy=False)
    client_id = fields.Char(required=True, index=True)
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    scope_id = fields.Many2one(
        "mcp.scope", required=True, ondelete="restrict",
        help="Effective governance scope resolved at consent time.")
    redirect_uri = fields.Char(required=True)
    code_challenge = fields.Char(required=True)
    code_challenge_method = fields.Char(default="S256")
    resource = fields.Char(help="RFC 8707 audience: the MCP endpoint URI.")
    scope = fields.Char(help="Granted OAuth scope string.")
    expires_at = fields.Datetime(required=True, index=True)
    used = fields.Boolean(default=False)

    @api.model
    def issue(self, vals):
        vals["expires_at"] = fields.Datetime.now() + timedelta(
            seconds=_ttl(self.env, PARAM_CODE_TTL, DEFAULT_CODE_TTL))
        return self.sudo().create(vals)

    def verify_pkce(self, code_verifier):
        """Return True when the presented verifier proves ownership (S256)."""
        self.ensure_one()
        if self.code_challenge_method != "S256":
            return False
        return constant_time_equals(
            self.code_challenge, pkce_s256_challenge(code_verifier))

    def is_expired(self):
        self.ensure_one()
        return fields.Datetime.now() > self.expires_at

    def is_valid(self):
        self.ensure_one()
        return bool(self) and not self.used and not self.is_expired()

    def consume(self):
        """Atomically claim this code. Returns False if it was already used.

        The UPDATE ... WHERE used = FALSE is the race guard: two concurrent
        token requests carrying the same code cannot both win, so a replay is
        always detected even under load.
        """
        self.ensure_one()
        self.env.cr.execute(
            "UPDATE mcp_oauth_authcode SET used = TRUE "
            "WHERE id = %s AND used = FALSE", (self.id,))
        won = bool(self.env.cr.rowcount)
        self.invalidate_recordset(["used"])
        return won

    def revoke_issued_tokens(self):
        """OAuth 2.1 §4.1.3.4: replaying a code revokes what it already minted.

        A replayed code means either the client is buggy or the code leaked;
        either way the tokens derived from it can no longer be trusted.
        """
        self.ensure_one()
        tokens = self.env["mcp.oauth.token"].sudo().search(
            [("authcode_id", "=", self.id), ("revoked", "=", False)])
        if tokens:
            _logger.warning(
                "Authorization code replay for client %s; revoking %d token(s)",
                self.client_id, len(tokens))
            tokens.write({"revoked": True})
        return tokens


class MCPOAuthToken(models.Model):
    """Access + refresh token pair, audience-bound to the MCP endpoint."""
    _name = "mcp.oauth.token"
    _description = "MCP OAuth Token"
    _order = "create_date desc"
    _rec_name = "user_id"

    access_token_hash = fields.Char(required=True, index=True, copy=False)
    refresh_token_hash = fields.Char(index=True, copy=False)
    client_id = fields.Char(required=True, index=True)
    client_name = fields.Char(
        help="Denormalised at issue time so the connected-clients list stays "
             "readable after a client record is archived.")
    authcode_id = fields.Many2one(
        "mcp.oauth.authcode", ondelete="set null", index=True,
        help="The authorization code this token chain descends from, so a "
             "replay of that code can revoke everything it minted.")
    user_id = fields.Many2one("res.users", required=True, ondelete="cascade", index=True)
    scope_id = fields.Many2one("mcp.scope", required=True, ondelete="restrict")
    resource = fields.Char(
        required=True,
        help="RFC 8707 audience the token was issued for. Checked on every "
             "MCP request; a token minted for another resource is refused.")
    scope = fields.Char()
    access_expires_at = fields.Datetime(required=True, index=True)
    refresh_expires_at = fields.Datetime()
    revoked = fields.Boolean(default=False, index=True)
    last_used = fields.Datetime(readonly=True)

    @api.model
    def issue(self, access_token, refresh_token, vals):
        now = fields.Datetime.now()
        vals = dict(vals)
        vals["access_token_hash"] = hash_secret(access_token)
        vals["refresh_token_hash"] = hash_secret(refresh_token) if refresh_token else False
        vals["access_expires_at"] = now + timedelta(
            seconds=_ttl(self.env, PARAM_ACCESS_TTL, DEFAULT_ACCESS_TTL))
        vals["refresh_expires_at"] = now + timedelta(
            seconds=_ttl(self.env, PARAM_REFRESH_TTL, DEFAULT_REFRESH_TTL))
        return self.sudo().create(vals)

    def is_access_valid(self, accepted_resources=None):
        """Token must be live, unrevoked and audience-matched (RFC 8707).

        ``accepted_resources`` is the set of URIs that identify *this* MCP
        server (it answers on more than one path). A token carrying anything
        else was minted for a different resource and MUST be refused - that is
        the confused-deputy protection the MCP spec calls out.
        """
        self.ensure_one()
        if self.revoked or fields.Datetime.now() > self.access_expires_at:
            return False
        if accepted_resources is not None:
            # Fail closed: an unbound token is not evidence of our audience.
            if not self.resource or self.resource not in set(accepted_resources):
                return False
        return True

    def is_refresh_valid(self):
        self.ensure_one()
        return bool(self.refresh_token_hash) and not self.revoked and (
            not self.refresh_expires_at or fields.Datetime.now() <= self.refresh_expires_at)

    def access_ttl_seconds(self):
        self.ensure_one()
        delta = self.access_expires_at - fields.Datetime.now()
        return max(0, int(delta.total_seconds()))

    def action_revoke(self):
        self.write({"revoked": True})

    @api.model
    def revoke_for_user(self, user_id):
        """Kill switch: drop every live token for one user."""
        tokens = self.sudo().search(
            [("user_id", "=", user_id), ("revoked", "=", False)])
        tokens.write({"revoked": True})
        return len(tokens)

    @api.model
    def cron_purge(self):
        """Vacuum expired codes and long-dead tokens (scheduled weekly)."""
        now = fields.Datetime.now()
        # Keep *used* codes until well past their expiry: they are what lets a
        # replay be detected and the derived tokens revoked. Only sweep codes
        # whose window has closed entirely.
        self.env["mcp.oauth.authcode"].sudo().search(
            [("expires_at", "<", now - timedelta(days=1))]).unlink()
        # Keep revoked tokens until their refresh window closes, so the audit
        # trail can still resolve who did what; then drop the dead rows.
        self.sudo().search([("refresh_expires_at", "<", now)]).unlink()
