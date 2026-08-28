# -*- coding: utf-8 -*-
"""The Connect screen's data layer.

The whole screen is rendered from get_state(), so testing that payload tests
the screen's behaviour without touching the browser.
"""
import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlparse

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models.mcp_connect import STARTER_PROMPTS
from ..models.mcp_scope import SUGGESTED_MODELS
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

    def _prompt_texts(self, user=None):
        connect = self.Connect.with_user(user) if user else self.Connect
        return [p["text"] for p in connect.get_state()["prompts"]]

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
        result = self.Connect.revoke(token.id)
        self.assertFalse([c for c in result["state"]["connections"]
                          if c["id"] == token.id])

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
        prompts = self._prompt_texts()
        self.assertTrue(prompts)
        self.assertFalse([p for p in prompts if p.startswith("Create")])

    def test_read_prompts_are_limited_to_models_in_the_matrix(self):
        """The seeded scope covers `base` models only, so the sales and
        invoicing suggestions must not be offered on a fresh install."""
        prompts = self._prompt_texts()
        readable = set(self.env["mcp.scope.line"].sudo().search(
            [("can_read", "=", True)]).mapped("model_name"))
        if "sale.order" not in readable:
            self.assertFalse([p for p in prompts if "sales orders" in p])
        if "account.move" not in readable:
            self.assertFalse([p for p in prompts if "invoices" in p])

    def test_write_prompts_appear_when_the_matrix_permits_that_model(self):
        scope = self.env["mcp.scope"].create(
            {"name": "TEST writable", "read_only": False})
        self.env["mcp.scope.line"].create({
            "scope_id": scope.id,
            "model_id": self.env["ir.model"]._get("res.partner").id,
            "can_read": True, "can_create": True,
        })
        self.assertTrue([p for p in self._prompt_texts()
                         if p.startswith("Create partners")])

    def test_write_prompt_stays_hidden_when_only_another_model_is_writable(self):
        """A writable scope is not enough - it has to be writable *for that model*.

        This is the chip that used to appear on any writable scope and then
        fail in the assistant, which reads as the product being broken.
        """
        scope = self.env["mcp.scope"].create(
            {"name": "TEST writable partners", "read_only": False})
        self.env["mcp.scope.line"].create({
            "scope_id": scope.id,
            "model_id": self.env["ir.model"]._get("res.partner").id,
            "can_read": True, "can_create": True,
        })
        prompts = self._prompt_texts()
        self.assertFalse([p for p in prompts if "sale order" in p])

    def test_prompts_that_need_no_model_are_always_offered(self):
        """However tight the scope, the screen must never show zero prompts."""
        self.env["mcp.scope.line"].sudo().search([]).unlink()
        self.assertTrue(self._prompt_texts())

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

    # -------------------------------------------------------------- payload
    def test_get_state_can_skip_the_qr(self):
        """The QR is rendered server-side and the URL cannot change between
        two polls, so a poll must not pay for it."""
        self.assertIn("qr", self.Connect.get_state())
        self.assertNotIn("qr", self.Connect.get_state(with_qr=False))

    def test_the_checklist_never_stops_early(self):
        """A list that just ends reads as one that has not finished running."""
        keys = {c["key"] for c in self.Connect.get_state()["checks"]}
        self.assertIn("enabled", keys)

    # ----------------------------------------------------- business models
    def _strip_business_models(self):
        """Reduce the effective scope to `base` models, as a pre-hook install
        had, and report whether this database has anything to add back."""
        scope = self.env.user.sudo().mcp_effective_scope()
        scope.line_ids.filtered(
            lambda l: l.model_name in SUGGESTED_MODELS).unlink()
        return scope, [m for m in SUGGESTED_MODELS if m in self.env]

    def test_a_base_only_scope_is_flagged_as_too_thin(self):
        """Connected and useless is the failure this row exists to catch."""
        _scope, installed = self._strip_business_models()
        if not installed:
            self.skipTest("no business app installed in this database")
        check = self._checks(self.Connect.get_state()).get("models_thin")
        self.assertTrue(check, "a scope with no business model must warn")
        self.assertEqual(check["state"], "warn", "it warns, it does not block")
        self.assertEqual(check["fix_method"], "add_suggested_models")

    def test_the_warning_goes_away_once_business_models_are_readable(self):
        scope, installed = self._strip_business_models()
        if not installed:
            self.skipTest("no business app installed in this database")
        self.assertIn("models_thin", self._checks(self.Connect.get_state()))
        self.Connect.add_suggested_models()
        self.assertNotIn("models_thin", self._checks(self.Connect.get_state()))
        self.assertTrue(set(scope.readable_model_names()) & set(installed))

    def test_the_warning_is_silent_on_a_deliberately_narrow_scope(self):
        """Once an administrator has opened the scope, a scope they then keep
        narrow is their decision, not a defect to nag about every page view."""
        _scope, installed = self._strip_business_models()
        if not installed:
            self.skipTest("no business app installed in this database")
        self.Connect.add_suggested_models()
        self.assertNotIn("models_thin", self._checks(self.Connect.get_state()))

    def test_adding_suggested_models_is_re_runnable(self):
        self._strip_business_models()
        before = len(self.Connect.add_suggested_models()["checks"])
        self.Connect.add_suggested_models()
        self.assertEqual(len(self.Connect.get_state()["checks"]), before)

    # ------------------------------------------------------ what it may do
    def test_writes_block_describes_the_effective_scope(self):
        writes = self.Connect.get_state()["writes"]
        for key in ("enabled", "requires_approval", "scope_name", "can_toggle",
                    "pending", "needs_reconnect"):
            self.assertIn(key, writes)

    def test_set_writes_flips_the_effective_scope(self):
        scope = self.env.user.sudo().mcp_effective_scope()
        self.Connect.set_writes(True)
        self.assertFalse(scope.read_only)
        self.assertTrue(self.Connect.get_state()["writes"]["enabled"])
        self.Connect.set_writes(False)
        self.assertTrue(scope.read_only)

    def test_set_writes_leaves_the_approval_gate_alone(self):
        """Turning writes on must not quietly turn the human gate off too."""
        scope = self.env.user.sudo().mcp_effective_scope()
        scope.require_approval = True
        self.Connect.set_writes(True)
        self.assertTrue(scope.require_approval)
        self.Connect.set_writes(False)

    def test_a_stale_connection_is_surfaced_as_needing_a_reconnect(self):
        """A scope change never reaches a live connection; saying so on a
        grey table row was not enough for anyone to notice."""
        token = self._token()
        token.scope_id.read_only = False
        token.scope = "odoo:read"
        writes = self.Connect.get_state()["writes"]
        self.assertTrue(writes["needs_reconnect"])
        self.assertIn("Reconnect", writes["reconnect_hint"])

    # ------------------------------------------------------- reachability
    def test_reachability_is_not_re_probed_inside_the_cache_window(self):
        """Every mount calls this; an unconditional probe is an outbound round
        trip and three parameter writes per page view, per user."""
        Param = self.env["ir.config_parameter"].sudo()
        Param.set_param("mcp_governance_suite.reachability_state", "ok")
        Param.set_param("mcp_governance_suite.reachability_detail", "cached")
        Param.set_param("mcp_governance_suite.reachability_checked_at",
                        fields.Datetime.to_string(fields.Datetime.now()))
        self.Connect.test_reachability()
        self.assertEqual(
            Param.get_param("mcp_governance_suite.reachability_detail"),
            "cached", "a fresh verdict must be reused untouched")

    def test_a_stale_verdict_is_re_probed(self):
        Param = self.env["ir.config_parameter"].sudo()
        Param.set_param("mcp_governance_suite.reachability_state", "ok")
        Param.set_param("mcp_governance_suite.reachability_detail", "stale")
        Param.set_param(
            "mcp_governance_suite.reachability_checked_at",
            fields.Datetime.to_string(
                fields.Datetime.now() - timedelta(minutes=60)))
        self.Connect.test_reachability()
        self.assertNotEqual(
            Param.get_param("mcp_governance_suite.reachability_detail"),
            "stale")

    def test_force_re_probes_even_when_fresh(self):
        Param = self.env["ir.config_parameter"].sudo()
        Param.set_param("mcp_governance_suite.reachability_state", "ok")
        Param.set_param("mcp_governance_suite.reachability_detail", "cached")
        Param.set_param("mcp_governance_suite.reachability_checked_at",
                        fields.Datetime.to_string(fields.Datetime.now()))
        self.Connect.test_reachability(force=True)
        self.assertNotEqual(
            Param.get_param("mcp_governance_suite.reachability_detail"),
            "cached")

    def test_a_corrupt_timestamp_does_not_break_the_page(self):
        Param = self.env["ir.config_parameter"].sudo()
        Param.set_param("mcp_governance_suite.reachability_state", "ok")
        Param.set_param("mcp_governance_suite.reachability_checked_at",
                        "not a date")
        self.Connect.test_reachability()  # must not raise

    # ---------------------------------------------------------- self test
    def test_self_test_reads_real_records_through_the_engine(self):
        result = self.Connect.run_self_test()
        self.assertTrue(result["ok"], result.get("message"))
        self.assertTrue(result["model"])

    def test_self_test_is_audited_as_a_self_test(self):
        """Audited honestly, so nobody later mistakes it for an assistant."""
        self.Connect.run_self_test()
        self.assertTrue(self.env["mcp.audit.log"].sudo().search_count(
            [("transport", "=", "selftest")]))

    def test_self_test_explains_a_scope_that_can_read_nothing(self):
        self.env.user.sudo().mcp_effective_scope().line_ids.unlink()
        result = self.Connect.run_self_test()
        self.assertFalse(result["ok"])
        self.assertTrue(result["message"])

    # --------------------------------------------------------- disconnect
    def test_revoke_reports_whether_it_actually_revoked(self):
        """It used to announce success over a connection still very much
        connected - not cosmetic, on a security control."""
        token = self._token()
        result = self.Connect.revoke(token.id)
        self.assertTrue(result["ok"])
        self.assertTrue(result["message"])
        self.assertFalse(
            [c for c in result["state"]["connections"] if c["id"] == token.id])

    def test_revoking_something_that_is_gone_is_not_reported_as_success(self):
        result = self.Connect.revoke(0)
        self.assertFalse(result["ok"])
        self.assertTrue(result["message"])

    # ------------------------------------------- the reader's own rights
    def test_prompts_respect_the_users_own_access_rights(self):
        """The matrix is only half the permission.

        A scope opened onto the whole business lists models plenty of people
        cannot see, so filtering on the matrix alone would offer a salesperson
        a stock question that comes back refused.
        """
        employee = self.env["res.users"].create({
            "name": "MCP Prompt Employee",
            "login": "mcp_prompt_employee",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        for prompt in self._prompt_texts(employee):
            for _needs_write, model, text in STARTER_PROMPTS:
                if text == prompt and model:
                    self.assertTrue(
                        self.env[model].with_user(employee).has_access("read"),
                        "offered '%s' but %s is not readable by them"
                        % (text, model))

    def test_self_test_picks_a_model_the_user_can_actually_read(self):
        employee = self.env["res.users"].create({
            "name": "MCP Selftest Employee",
            "login": "mcp_selftest_employee",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        result = self.Connect.with_user(employee).run_self_test()
        if result["ok"]:
            self.assertTrue(
                self.env[result["model"]].with_user(employee).has_access("read"))

    # ------------------------------------------------------- quick setup
    def _guides(self):
        return {g["key"]: g for g in self.Connect.get_state()["clients"]}

    def test_vscode_install_link_carries_the_real_server_config(self):
        """VS Code documents vscode:mcp/install?<url-encoded JSON>."""
        guide = self._guides()["vscode"]
        self.assertTrue(guide["install_url"].startswith("vscode:mcp/install?"))
        payload = json.loads(unquote(guide["install_url"].split("?", 1)[1]))
        self.assertEqual(payload["type"], "http")
        self.assertTrue(payload["url"].endswith("/mcp"))
        self.assertTrue(payload["name"])

    def test_cursor_install_link_carries_the_real_server_config(self):
        """Cursor takes the name as a parameter and the server base64-encoded."""
        guide = self._guides()["cursor"]
        self.assertTrue(guide["install_url"].startswith(
            "cursor://anysphere.cursor-deeplink/mcp/install?"))
        query = parse_qs(urlparse(guide["install_url"]).query)
        self.assertTrue(query["name"][0])
        config = json.loads(base64.urlsafe_b64decode(query["config"][0] + "==="))
        self.assertTrue(config["url"].endswith("/mcp"))

    def test_no_install_link_is_invented_where_none_exists(self):
        """Claude and ChatGPT have no connector-install deep link.

        A button that opened a chat saying "connect this MCP server" would
        produce an assistant explaining it cannot, under a label promising
        one-click setup - worse than the honest steps.
        """
        guides = self._guides()
        for key in ("claude", "chatgpt", "other"):
            self.assertNotIn("install_url", guides[key])

    def test_install_link_points_at_the_public_address(self):
        """The same address the hero shows, not web.base.url."""
        state = self.Connect.get_state()
        payload = json.loads(unquote(
            {g["key"]: g for g in state["clients"]}["vscode"]
            ["install_url"].split("?", 1)[1]))
        self.assertEqual(payload["url"], state["urls"]["mcp"])

    def test_every_prompt_can_be_opened_in_an_assistant(self):
        for prompt in self.Connect.get_state()["prompts"]:
            self.assertTrue(prompt["ask"], prompt["text"])
            for ask in prompt["ask"]:
                self.assertTrue(ask["url"].startswith("https://"))
                self.assertIn("q=", ask["url"])

    def test_the_ask_link_round_trips_the_prompt_text(self):
        prompt = self.Connect.get_state()["prompts"][0]
        for ask in prompt["ask"]:
            sent = unquote(urlparse(ask["url"]).query.split("=", 1)[1])
            self.assertEqual(sent, prompt["text"])

    def test_ask_links_carry_only_canned_text(self):
        """These travel to a third party and land in their logs, so nothing
        but the module's own constants may ever go into that query string."""
        canned = {text for _w, _m, text in STARTER_PROMPTS}
        for prompt in self.Connect.get_state()["prompts"]:
            self.assertIn(prompt["text"], canned)

    def test_writes_on_with_an_empty_matrix_is_reported_as_inert(self):
        """The dead end: the kill switch is off, so the screen says "read and
        write", but no matrix row grants anything and every write is refused.
        The assistant reports itself read-only and the user believes the
        toggle did nothing. Both true; only this row reconciles them."""
        scope = self.env.user.sudo().mcp_effective_scope()
        scope.line_ids.write({"can_create": False, "can_write": False,
                              "can_unlink": False})
        self.Connect.set_writes(True)
        writes = self.Connect.get_state()["writes"]
        self.assertTrue(writes["enabled"])
        self.assertEqual(writes["writable_models"], 0)
        self.assertTrue(writes["inert"])
        self.Connect.set_writes(False)

    def test_writes_are_not_inert_once_a_model_permits_them(self):
        scope = self.env.user.sudo().mcp_effective_scope()
        scope.line_ids[:1].write({"can_create": True})
        self.Connect.set_writes(True)
        writes = self.Connect.get_state()["writes"]
        self.assertTrue(writes["writable_models"] >= 1)
        self.assertFalse(writes["inert"])
        self.Connect.set_writes(False)

    def test_read_only_is_never_reported_as_inert(self):
        """Inert means "on but useless", not "off"."""
        self.Connect.set_writes(False)
        self.assertFalse(self.Connect.get_state()["writes"]["inert"])
