# -*- coding: utf-8 -*-
"""End-to-end HTTP tests for the MCP + OAuth endpoints."""
import json

from odoo.tests import HttpCase, tagged

from ..models.tools_crypto import hash_secret, new_secret

MODERN = "2026-07-28"
LEGACY = "2025-06-18"
META_VERSION = "io.modelcontextprotocol/protocolVersion"


@tagged("post_install", "-at_install")
class TestHttpEndpoints(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scope = cls.env.ref("mcp_governance_suite.scope_readonly_default")
        cls.raw_key = new_secret(prefix="mcp-", nbytes=32)
        cls.key = cls.env["mcp.api.key"].create({
            "name": "TEST http key",
            "user_id": cls.env.ref("base.user_admin").id,
            "scope_id": cls.scope.id,
            "key_hash": hash_secret(cls.raw_key),
            "key_preview": cls.raw_key[:12] + "...",
        })

    # ------------------------------------------------------------------ helpers
    def _post(self, body, headers=None, url="/mcp"):
        base = {"Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.raw_key}
        base.update(headers or {})
        return self.url_open(url, data=json.dumps(body), headers=base,
                             allow_redirects=False)

    def _modern(self, method, params=None, version=MODERN, name=None,
                headers=None, with_meta=True):
        params = dict(params or {})
        if with_meta:
            params["_meta"] = {META_VERSION: version}
        hdrs = {"MCP-Protocol-Version": version, "Mcp-Method": method}
        if name is not None:
            hdrs["Mcp-Name"] = name
        hdrs.update(headers or {})
        return self._post(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, hdrs)

    # ---------------------------------------------------------------- discovery
    def test_health(self):
        r = self.url_open("/mcp/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["server"]["name"], "odoo-mcp-governance")
        self.assertIn("oauth", data["authMethods"])
        self.assertIn(MODERN, data["protocolVersions"])

    def test_protected_resource_metadata(self):
        r = self.url_open("/.well-known/oauth-protected-resource")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["resource"].endswith("/mcp"))
        self.assertTrue(data["authorization_servers"])
        # Refresh tokens are not a resource requirement (MCP: Refresh Tokens).
        self.assertNotIn("offline_access", data["scopes_supported"])

    def test_path_scoped_metadata_echoes_its_own_resource(self):
        """RFC 9728 §3.3: the client validates `resource` against the identifier
        it derived this URL from, so each path must answer for itself."""
        for suffix in ("/mcp", "/mcp/v1"):
            r = self.url_open("/.well-known/oauth-protected-resource" + suffix)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(
                r.json()["resource"].endswith(suffix),
                "metadata at %s must claim the matching resource" % suffix)

    def test_authorization_server_metadata(self):
        r = self.url_open("/.well-known/oauth-authorization-server")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("token_endpoint", data)
        self.assertEqual(data["code_challenge_methods_supported"], ["S256"])
        self.assertNotIn("plain", data["code_challenge_methods_supported"])
        self.assertIn("revocation_endpoint", data)
        # RFC 9207 + CIMD advertisement.
        self.assertTrue(data["authorization_response_iss_parameter_supported"])
        self.assertTrue(data["client_id_metadata_document_supported"])
        # issuer MUST string-match the iss we emit on authorization responses.
        self.assertEqual(data["issuer"].rstrip("/"), data["issuer"])

    # --------------------------------------------------------------- auth gates
    def test_mcp_requires_auth_with_www_authenticate(self):
        r = self.url_open(
            "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            headers={"Content-Type": "application/json"},
            allow_redirects=False)
        self.assertEqual(r.status_code, 401)
        challenge = r.headers.get("WWW-Authenticate", "")
        # RFC 9728: point the client at the resource metadata to start OAuth.
        self.assertIn("resource_metadata", challenge)
        # Tell it which scope to ask for, so it does not have to guess.
        self.assertIn('scope="odoo:read"', challenge)

    def test_get_stream_is_not_allowed(self):
        """2026-07-28 removed the standalone GET stream from this transport."""
        self.assertEqual(self.url_open("/mcp", allow_redirects=False).status_code, 405)

    # ------------------------------------------------------------ modern era
    def test_server_discover(self):
        r = self._modern("server/discover")
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertIn(MODERN, result["supportedVersions"])
        self.assertIn("capabilities", result)
        self.assertEqual(
            result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
            "odoo-mcp-governance")

    def test_modern_request_works_without_a_handshake(self):
        r = self._modern("tools/list")
        self.assertEqual(r.status_code, 200)
        self.assertIn("tools", r.json()["result"])
        # Protocol-level sessions are gone: never mint or echo a session id.
        self.assertNotIn("Mcp-Session-Id", r.headers)

    def test_unsupported_protocol_version(self):
        r = self._modern("tools/list", version="1900-01-01")
        self.assertEqual(r.status_code, 400)
        error = r.json()["error"]
        self.assertEqual(error["code"], -32022)
        self.assertIn(MODERN, error["data"]["supported"])
        self.assertEqual(error["data"]["requested"], "1900-01-01")

    def test_header_body_mismatch_is_rejected(self):
        """An intermediary routing on headers and a server executing on the body
        must never be able to disagree."""
        r = self._modern("tools/list", headers={"Mcp-Method": "tools/call"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], -32020)

    def test_modern_request_requires_protocol_version_header(self):
        r = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
             "params": {"_meta": {META_VERSION: MODERN}}},
            {"Mcp-Method": "tools/list"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], -32020)

    def test_mcp_name_header_must_match_body(self):
        r = self._modern("tools/call", params={"name": "list_models"},
                         name="something_else")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], -32020)

    def test_unknown_method_is_404_for_modern_clients(self):
        r = self._modern("does/not/exist")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], -32601)

    # ------------------------------------------------------------- legacy era
    def test_legacy_initialize_still_works(self):
        """Legacy clients have no fall-forward path, so this must keep working."""
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": LEGACY}})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertEqual(result["protocolVersion"], LEGACY)
        self.assertIn("serverInfo", result)
        # Legacy clients track a session id; they still get one.
        self.assertIn("Mcp-Session-Id", r.headers)

    def test_legacy_tools_list_without_meta(self):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("tools", r.json()["result"])

    # ------------------------------------------------------------------ origin
    def test_hostile_origin_is_refused(self):
        """DNS-rebinding guard: a hostile page must not drive this endpoint."""
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       {"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)

    def test_same_origin_is_allowed(self):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       {"Origin": self.base_url()})
        self.assertEqual(r.status_code, 200)

    # -------------------------------------------------------------- oauth flow
    def test_dynamic_client_registration(self):
        r = self.url_open(
            "/oauth/register",
            data=json.dumps({
                "client_name": "Test",
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
            }),
            headers={"Content-Type": "application/json"},
            allow_redirects=False)
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertTrue(body["client_id"].startswith("mcpc-"))
        self.assertEqual(body["token_endpoint_auth_method"], "none")

    def test_registration_rejects_loopback_lookalike(self):
        r = self.url_open(
            "/oauth/register",
            data=json.dumps({
                "client_name": "Evil",
                "redirect_uris": ["http://localhost.attacker.example/cb"],
            }),
            headers={"Content-Type": "application/json"},
            allow_redirects=False)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_redirect_uri")

    def test_revocation_endpoint_accepts_unknown_token(self):
        """RFC 7009 §2.2: answer 200 either way - never confirm existence."""
        client = self.env["mcp.oauth.client"].create({
            "name": "Revoker", "client_id": "mcpc-revoke-test",
            "token_endpoint_auth_method": "none",
            "redirect_uris": "https://x.example/cb",
        })
        r = self.url_open(
            "/oauth/revoke",
            data=json.dumps({"client_id": client.client_id,
                             "token": "mcpat-never-existed"}),
            headers={"Content-Type": "application/json"},
            allow_redirects=False)
        self.assertEqual(r.status_code, 200)

    def test_revocation_kills_a_live_token(self):
        client = self.env["mcp.oauth.client"].create({
            "name": "Revoker2", "client_id": "mcpc-revoke-live",
            "token_endpoint_auth_method": "none",
            "redirect_uris": "https://x.example/cb",
        })
        access = new_secret(prefix="mcpat-")
        token = self.env["mcp.oauth.token"].issue(access, new_secret(), {
            "client_id": client.client_id,
            "user_id": self.env.ref("base.user_admin").id,
            "scope_id": self.scope.id,
            "resource": self.base_url().rstrip("/") + "/mcp",
        })
        r = self.url_open(
            "/oauth/revoke",
            data=json.dumps({"client_id": client.client_id, "token": access}),
            headers={"Content-Type": "application/json"},
            allow_redirects=False)
        self.assertEqual(r.status_code, 200)
        token.invalidate_recordset(["revoked"])
        self.assertTrue(token.revoked)
