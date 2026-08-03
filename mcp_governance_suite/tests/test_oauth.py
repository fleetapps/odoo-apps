# -*- coding: utf-8 -*-
"""OAuth 2.1 / PKCE / CIMD unit tests at the model layer."""
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..controllers.oauth import (
    SCOPE_READ,
    SCOPE_WRITE,
    is_valid_redirect,
    normalize_scopes,
)
from ..models.mcp_oauth import (
    _assert_public_host,
    fetch_cimd_document,
    is_cimd_client_id,
)
from ..models.tools_crypto import (
    hash_secret,
    new_secret,
    pkce_s256_challenge,
)

RESOURCE = "https://host/mcp"
RESOURCES = {"https://host/mcp", "https://host/mcp/v1"}


@tagged("post_install", "-at_install")
class TestOAuth(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = cls.env.ref("base.user_admin")
        cls.scope = cls.env["mcp.scope"].create({"name": "TEST oauth scope"})
        cls.client = cls.env["mcp.oauth.client"].create({
            "name": "Test Client",
            "client_id": "mcpc-test",
            "token_endpoint_auth_method": "none",
            "redirect_uris": "https://claude.ai/callback\nhttp://localhost:9000/cb",
        })

    def _authcode(self, **overrides):
        vals = {
            "code_hash": hash_secret(new_secret(prefix="mcpac-")),
            "client_id": self.client.client_id,
            "user_id": self.user.id,
            "scope_id": self.scope.id,
            "redirect_uri": "https://claude.ai/callback",
            "code_challenge": "x",
            "resource": RESOURCE,
        }
        vals.update(overrides)
        return self.env["mcp.oauth.authcode"].issue(vals)

    def _token(self, authcode=None, **overrides):
        vals = {
            "client_id": self.client.client_id,
            "user_id": self.user.id,
            "scope_id": self.scope.id,
            "resource": RESOURCE,
            "authcode_id": authcode.id if authcode else False,
        }
        vals.update(overrides)
        return self.env["mcp.oauth.token"].issue(
            new_secret(prefix="mcpat-"), new_secret(prefix="mcprt-"), vals)

    # ------------------------------------------------------------- client model
    def test_redirect_uri_exact_match(self):
        self.assertTrue(self.client.validate_redirect_uri("https://claude.ai/callback"))
        self.assertTrue(self.client.validate_redirect_uri("http://localhost:9000/cb"))
        self.assertFalse(self.client.validate_redirect_uri("https://claude.ai/callback/evil"))
        self.assertFalse(self.client.validate_redirect_uri("https://evil.example/cb"))
        self.assertFalse(self.client.validate_redirect_uri(""))

    def test_loopback_lookalike_is_rejected(self):
        """A prefix check would accept these; a parsed host check must not."""
        self.assertTrue(is_valid_redirect("http://localhost:3000/cb"))
        self.assertTrue(is_valid_redirect("http://127.0.0.1/cb"))
        self.assertTrue(is_valid_redirect("https://claude.ai/cb"))
        # The bug this guards: "http://localhost".startswith matches all of these.
        self.assertFalse(is_valid_redirect("http://localhost.attacker.example/cb"))
        self.assertFalse(is_valid_redirect("http://localhost@attacker.example/cb"))
        self.assertFalse(is_valid_redirect("http://evil.example/cb"))
        # Non-loopback plain HTTP, other schemes, and fragments are all out.
        self.assertFalse(is_valid_redirect("http://example.com/cb"))
        self.assertFalse(is_valid_redirect("javascript:alert(1)"))
        self.assertFalse(is_valid_redirect("https://claude.ai/cb#frag"))
        self.assertFalse(is_valid_redirect(""))

    def test_public_client_needs_no_secret(self):
        self.assertTrue(self.client.check_secret(None))

    def test_confidential_client_checks_secret(self):
        secret = "mcps-shhh"
        conf = self.env["mcp.oauth.client"].create({
            "name": "Conf", "client_id": "mcpc-conf",
            "token_endpoint_auth_method": "client_secret_post",
            "client_secret_hash": hash_secret(secret),
            "redirect_uris": "https://x.example/cb",
        })
        self.assertTrue(conf.check_secret(secret))
        self.assertFalse(conf.check_secret("wrong"))

    # -------------------------------------------------------------------- PKCE
    def test_pkce_s256_roundtrip(self):
        verifier = new_secret(nbytes=32)
        authcode = self._authcode(code_challenge=pkce_s256_challenge(verifier))
        self.assertTrue(authcode.is_valid())
        self.assertTrue(authcode.verify_pkce(verifier))
        self.assertFalse(authcode.verify_pkce("the-wrong-verifier"))

    def test_plain_pkce_is_refused(self):
        """S256 only - a `plain` challenge must never verify."""
        authcode = self._authcode(code_challenge="verifier",
                                  code_challenge_method="plain")
        self.assertFalse(authcode.verify_pkce("verifier"))

    # ------------------------------------------------------------- code replay
    def test_authcode_consume_is_single_use(self):
        authcode = self._authcode()
        self.assertTrue(authcode.is_valid())
        self.assertTrue(authcode.consume(), "first redemption should win")
        self.assertFalse(authcode.consume(), "replay must lose the race")
        self.assertFalse(authcode.is_valid())

    def test_replay_revokes_tokens_minted_from_that_code(self):
        """OAuth 2.1 §4.1.3.4: a replayed code invalidates what it produced."""
        authcode = self._authcode()
        authcode.consume()
        token = self._token(authcode=authcode)
        self.assertTrue(token.is_access_valid(accepted_resources=RESOURCES))

        # Second redemption of the same code.
        self.assertFalse(authcode.consume())
        authcode.revoke_issued_tokens()
        self.assertTrue(token.revoked)
        self.assertFalse(token.is_access_valid(accepted_resources=RESOURCES))
        self.assertFalse(token.is_refresh_valid())

    # ------------------------------------------------------------------ tokens
    def test_token_issue_and_audience_binding(self):
        access, refresh = new_secret(prefix="mcpat-"), new_secret(prefix="mcprt-")
        token = self.env["mcp.oauth.token"].issue(access, refresh, {
            "client_id": self.client.client_id,
            "user_id": self.user.id,
            "scope_id": self.scope.id,
            "resource": RESOURCE,
        })
        # Stored as hashes, never plaintext.
        self.assertEqual(token.access_token_hash, hash_secret(access))
        self.assertTrue(token.is_access_valid(accepted_resources=RESOURCES))
        # RFC 8707: a token minted for another resource must be refused.
        self.assertFalse(token.is_access_valid(
            accepted_resources={"https://other/mcp"}))
        self.assertTrue(token.is_refresh_valid())

    def test_both_endpoint_paths_are_valid_audiences(self):
        """The endpoint answers on /mcp and /mcp/v1; either may be the audience.

        Getting this wrong means a client that connects to /mcp/v1 receives a
        token that can never authenticate - a permanent 401 with no diagnostic.
        """
        for resource in ("https://host/mcp", "https://host/mcp/v1"):
            token = self._token(resource=resource)
            self.assertTrue(
                token.is_access_valid(accepted_resources=RESOURCES),
                "%s should be accepted as our audience" % resource)

    def test_revoked_token_is_invalid(self):
        token = self._token()
        token.action_revoke()
        self.assertFalse(token.is_access_valid())
        self.assertFalse(token.is_refresh_valid())

    def test_revoke_for_user_kill_switch(self):
        self._token()
        self._token()
        self.assertEqual(
            self.env["mcp.oauth.token"].revoke_for_user(self.user.id), 2)
        self.assertFalse(self.env["mcp.oauth.token"].search(
            [("user_id", "=", self.user.id), ("revoked", "=", False)]))

    # ------------------------------------------------------------------ scopes
    def test_scope_normalisation(self):
        self.assertEqual(normalize_scopes("odoo:read odoo:write"),
                         [SCOPE_READ, SCOPE_WRITE])
        # Legacy dot-separated names from earlier builds still resolve.
        self.assertEqual(normalize_scopes("odoo.write"), [SCOPE_WRITE])
        self.assertEqual(normalize_scopes("odoo"), [SCOPE_READ])
        # Unknown scopes are dropped, and the default is least privilege.
        self.assertEqual(normalize_scopes("admin:everything"), [SCOPE_READ])
        self.assertEqual(normalize_scopes(""), [SCOPE_READ])
        self.assertEqual(normalize_scopes(None), [SCOPE_READ])

    # -------------------------------------------------------------------- CIMD
    def test_cimd_client_id_shape(self):
        self.assertTrue(is_cimd_client_id("https://app.example.com/client.json"))
        # Must be https, must carry a path, must not carry a fragment.
        self.assertFalse(is_cimd_client_id("http://app.example.com/client.json"))
        self.assertFalse(is_cimd_client_id("https://app.example.com"))
        self.assertFalse(is_cimd_client_id("https://app.example.com/"))
        self.assertFalse(is_cimd_client_id("https://app.example.com/c.json#x"))
        self.assertFalse(is_cimd_client_id("mcpc-opaque-id"))
        self.assertFalse(is_cimd_client_id(""))

    def test_cimd_refuses_private_hosts(self):
        """SSRF guard: a hostile client_id must not make Odoo probe its LAN."""
        for url in ("https://localhost/c.json",
                    "https://127.0.0.1/c.json",
                    "https://[::1]/c.json"):
            with self.assertRaises(UserError, msg="%s should be refused" % url):
                _assert_public_host(url)

    def test_cimd_rejects_non_url_client_id(self):
        with self.assertRaises(UserError):
            fetch_cimd_document("mcpc-not-a-url")

    def test_cimd_metadata_mapping(self):
        """Client-published metadata is stored, but never trusted to grant."""
        doc = {
            "client_id": "https://app.example.com/client.json",
            "client_name": "Example MCP Client",
            "client_uri": "https://app.example.com",
            "redirect_uris": ["http://localhost:3000/callback"],
            "token_endpoint_auth_method": "none",
        }
        client = self.env["mcp.oauth.client"]._create_from_cimd(doc)
        self.assertEqual(client.registration_type, "cimd")
        self.assertEqual(client.client_id, doc["client_id"])
        self.assertTrue(client.validate_redirect_uri("http://localhost:3000/callback"))
        self.assertFalse(client.validate_redirect_uri("https://evil.example/cb"))
        self.assertTrue(client.metadata_fetched_at)

    def test_cimd_refresh_re_validates_redirect_uris(self):
        """Dropping a callback from the document must drop it here too."""
        doc = {
            "client_id": "https://app.example.com/c2.json",
            "client_name": "Example",
            "redirect_uris": ["https://a.example/cb", "https://b.example/cb"],
        }
        client = self.env["mcp.oauth.client"]._create_from_cimd(doc)
        self.assertTrue(client.validate_redirect_uri("https://b.example/cb"))
        client._apply_cimd(dict(doc, redirect_uris=["https://a.example/cb"]))
        self.assertFalse(client.validate_redirect_uri("https://b.example/cb"))
