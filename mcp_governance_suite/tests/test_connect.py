# -*- coding: utf-8 -*-
"""The Connect screen's data layer.

The whole screen is rendered from get_state(), so testing that payload tests
the screen's behaviour without touching the browser.
"""
from odoo.tests import TransactionCase, tagged

from ..models.tools_crypto import hash_secret, new_secret


@tagged("post_install", "-at_install")
class TestConnectState(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Connect = cls.env["mcp.connect"]
        cls.admin = cls.env.ref("base.user_admin")
        cls.scope = cls.env.ref("mcp_governance_suite.scope_readonly_default")

    def _checks(self, state):
        return {c["key"]: c for c in state["checks"]}

    # -------------------------------------------------------------- payload
    def test_state_has_everything_the_screen_renders(self):
        state = self.Connect.get_state()
        for key in ("checks", "ready", "urls", "status", "connections",
                    "prompts", "clients", "can_admin"):
            self.assertIn(key, state)
        self.assertTrue(state["urls"]["mcp"].endswith("/mcp"))

    def test_get_state_makes_no_outbound_request(self):
        """It is polled every few seconds - a network call here would hang it.

        Reachability is therefore 'unknown' until test_reachability() runs.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "mcp_governance_suite.reachability_state", "")
        self.assertEqual(self._checks(self.Connect.get_state())["reach"]["state"],
                         "unknown")

    # ------------------------------------------------------------ readiness
    def test_localhost_base_url_is_flagged(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "http://localhost:8069")
        check = self._checks(self.Connect.get_state())["base_url"]
        self.assertEqual(check["state"], "fail")
        self.assertTrue(check["fix_action"], "a failing check must offer a fix")

    def test_plain_http_base_url_is_flagged(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "http://erp.example.com")
        self.assertEqual(
            self._checks(self.Connect.get_state())["base_url"]["state"], "fail")

    def test_public_https_base_url_passes(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://erp.example.com")
        self.assertEqual(
            self._checks(self.Connect.get_state())["base_url"]["state"], "ok")

    def test_every_failing_check_is_actionable(self):
        """A blocker the user cannot act on is just noise."""
        self.env["ir.config_parameter"].sudo().set_param("web.base.url", "")
        for check in self.Connect.get_state()["checks"]:
            if check["state"] == "fail":
                self.assertTrue(
                    check["detail"],
                    "check '%s' fails without explaining why" % check["key"])

    def test_ready_is_false_while_a_check_fails(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "http://localhost:8069")
        self.assertFalse(self.Connect.get_state()["ready"])

    # --------------------------------------------------------- live status
    def test_status_waits_before_any_connection(self):
        self.env["mcp.oauth.token"].sudo().search([]).unlink()
        self.env["mcp.api.key"].sudo().search([]).write({"active": False})
        self.assertEqual(self.Connect.get_state()["status"]["state"], "waiting")

    def test_status_flips_to_connected_when_a_token_lands(self):
        self._token()
        state = self.Connect.get_state()
        self.assertEqual(state["status"]["state"], "connected")
        self.assertTrue(state["connections"])

    def test_fresh_token_with_no_last_used_does_not_break_status(self):
        """last_used is empty until the first call; the status must survive it."""
        self._token()
        self.assertEqual(self.Connect.get_state()["status"]["state"], "connected")

    def test_revoke_removes_the_connection(self):
        token = self._token()
        state = self.Connect.revoke(token.id)
        self.assertFalse([c for c in state["connections"] if c["id"] == token.id])

    def test_revoke_all_disconnects_everything(self):
        self._token()
        self._token()
        self.assertFalse(self.Connect.revoke_all()["connections"])

    def _token(self):
        client = self.env["mcp.oauth.client"].search([], limit=1) or \
            self.env["mcp.oauth.client"].create({
                "name": "TEST client", "client_id": "mcpc-connect-test",
                "redirect_uris": "https://x.example/cb"})
        return self.env["mcp.oauth.token"].issue(
            new_secret(prefix="mcpat-"), new_secret(prefix="mcprt-"), {
                "client_id": client.client_id,
                "client_name": client.name,
                "user_id": self.admin.id,
                "scope_id": self.scope.id,
                "resource": "https://host/mcp",
            })

    # ------------------------------------------------------------- content
    def test_write_prompts_hidden_when_nothing_is_writable(self):
        """Never advertise a prompt the current permissions would refuse."""
        self.env["mcp.scope"].sudo().search(
            [("read_only", "=", False)]).write({"read_only": True})
        prompts = self.Connect.get_state()["prompts"]
        self.assertTrue(prompts)
        self.assertFalse([p for p in prompts if p.startswith("Create")])

    def test_write_prompts_appear_when_a_writable_scope_exists(self):
        self.env["mcp.scope"].create(
            {"name": "TEST writable", "read_only": False})
        self.assertTrue([p for p in self.Connect.get_state()["prompts"]
                         if p.startswith("Create")])

    def test_every_client_guide_has_steps(self):
        for guide in self.Connect.get_state()["clients"]:
            self.assertTrue(guide["steps"], guide["key"])
            self.assertTrue(guide["name"])

    def test_local_client_guides_carry_a_config_block(self):
        guides = {g["key"]: g for g in self.Connect.get_state()["clients"]}
        for key in ("vscode", "cursor"):
            self.assertIn("mcpServers", guides[key]["config"])

    def test_api_key_only_setup_still_reads_as_connected(self):
        self.env["mcp.oauth.token"].sudo().search([]).unlink()
        self.env["mcp.api.key"].create({
            "name": "TEST headless",
            "user_id": self.admin.id,
            "scope_id": self.scope.id,
            "key_hash": hash_secret(new_secret()),
        })
        self.assertEqual(self.Connect.get_state()["status"]["state"], "connected")
