# -*- coding: utf-8 -*-
"""Engine governance tests: scoping, blacklists, read-only, approval, limits.

Odoo testing reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/testing.html
"""
import json

from odoo.tests import TransactionCase, tagged

from ..models.mcp_engine import (
    SCOPE_READ,
    SCOPE_WRITE,
    MCPInsufficientScope,
)


@tagged("post_install", "-at_install")
class TestEngineGovernance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["mcp.engine"]
        cls.partner_model = cls.env["ir.model"]._get("res.partner")

        # A limited internal user: standard rights, nothing special.
        cls.user = cls.env["res.users"].create({
            "name": "MCP Test User",
            "login": "mcp_test_user",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

        cls.read_scope = cls.env["mcp.scope"].create({
            "name": "TEST read partners",
            "read_only": True,
            "require_approval": True,
            "rate_limit_per_hour": 0,
            "max_records": 50,
            "line_ids": [(0, 0, {
                "model_id": cls.partner_model.id,
                "can_read": True,
                "field_blacklist": "email",
            })],
        })

    def _key(self, scope, user=None):
        return self.env["mcp.api.key"].create({
            "name": "test key",
            "user_id": (user or self.user).id,
            "scope_id": scope.id,
        })

    def _call(self, scope, name, args, user=None, ctx=None):
        return self.Engine.with_user(user or self.user).call_tool(
            scope, name, args, ctx or {})

    def _payload(self, result):
        return json.loads(result["content"][0]["text"])

    # --------------------------------------------------------------- read path
    def test_search_respects_scope_and_blacklist(self):
        res = self._call(self.read_scope, "search_records", {
            "model": "res.partner", "fields": ["name", "email"], "limit": 5})
        self.assertFalse(res["isError"])
        payload = self._payload(res)
        self.assertIn("records", payload)
        for row in payload["records"]:
            self.assertNotIn("email", row, "blacklisted field must be stripped")

    def test_search_denied_model_not_in_scope(self):
        """A refusal has to name the model and where the switch lives.

        An opaque "access denied" is the most common complaint about ERP MCP
        servers: the assistant cannot act on it and the user cannot either.
        """
        res = self._call(self.read_scope, "search_records", {"model": "res.users"})
        self.assertTrue(res["isError"])
        message = self._payload(res)["message"]
        self.assertIn("res.users", message)
        self.assertIn("Model Permissions", message)

    def test_get_schema_hides_blacklist(self):
        res = self._call(self.read_scope, "get_schema", {"model": "res.partner"})
        fields = self._payload(res)["fields"]
        self.assertIn("name", fields)
        self.assertNotIn("email", fields)

    def test_read_group_aggregates(self):
        res = self._call(self.read_scope, "read_group", {
            "model": "res.partner", "group_by": ["is_company"],
            "measures": ["__count"]})
        self.assertFalse(res["isError"])
        self.assertIn("groups", self._payload(res))

    # -------------------------------------------------------------- write path
    def test_write_tool_hidden_in_read_only(self):
        tools = self.Engine.list_tools(self.read_scope)
        names = {t["name"] for t in tools}
        self.assertIn("search_records", names)
        self.assertNotIn("create_record", names,
                         "read-only scope must not advertise writes")

    def _write_scope(self):
        return self.env["mcp.scope"].create({
            "name": "TEST write partners",
            "read_only": False,
            "require_approval": False,
            "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "can_read": True, "can_create": True})],
        })

    def test_write_tool_listed_even_without_oauth_write(self):
        """A tool the client cannot see is one it can never ask permission for.

        Hiding write tools from a read-scoped token used to strand connections:
        the client pinned the challenged scopes, never saw a write tool, and so
        never triggered the step-up flow that would have widened them.
        """
        names = {t["name"] for t in self.Engine.list_tools(self._write_scope())}
        self.assertIn("create_record", names)

    def test_write_without_oauth_scope_raises_challenge(self):
        with self.assertRaises(MCPInsufficientScope) as caught:
            self._call(self._write_scope(), "create_record",
                       {"model": "res.partner", "values": {"name": "X"}},
                       ctx={"granted_scopes": [SCOPE_READ]})
        self.assertEqual(caught.exception.required, [SCOPE_WRITE])

    def test_write_allowed_with_oauth_write_scope(self):
        res = self._call(self._write_scope(), "create_record",
                         {"model": "res.partner", "values": {"name": "AI Co"}},
                         user=self.env.ref("base.user_admin"),
                         ctx={"granted_scopes": [SCOPE_READ, SCOPE_WRITE]})
        self.assertFalse(res["isError"])
        self.assertTrue(self._payload(res)["id"])

    def _write_capability(self, payload):
        return next(c for c in payload["capabilities"]
                    if c["technical_name"] == "data_write")

    def test_list_capabilities_explains_read_only(self):
        payload = self._payload(
            self._call(self.read_scope, "list_capabilities", {}))
        cap = self._write_capability(payload)
        self.assertEqual(cap["tools"], [], "read-only hides mutating tools")
        self.assertIn("Read Only", cap["unavailable_reason"],
                      "an empty capability must say which switch hid it")

    def test_list_capabilities_explains_missing_oauth_scope(self):
        payload = self._payload(self._call(
            self._write_scope(), "list_capabilities", {},
            ctx={"granted_scopes": [SCOPE_READ]}))
        cap = self._write_capability(payload)
        self.assertTrue(cap["tools"], "tools stay listed so step-up can happen")
        self.assertNotIn("unavailable_reason", cap)
        self.assertIn(SCOPE_WRITE, cap["needs_authorization"])

    def test_list_capabilities_clean_when_fully_granted(self):
        payload = self._payload(self._call(
            self._write_scope(), "list_capabilities", {},
            ctx={"granted_scopes": [SCOPE_READ, SCOPE_WRITE]}))
        cap = self._write_capability(payload)
        self.assertTrue(cap["tools"])
        self.assertNotIn("unavailable_reason", cap)
        self.assertNotIn("needs_authorization", cap)

    def test_write_blocked_in_read_only(self):
        res = self._call(self.read_scope, "create_record", {
            "model": "res.partner", "values": {"name": "X"}})
        self.assertTrue(res["isError"])
        self.assertIn("read-only", self._payload(res)["message"])

    def test_write_is_approval_gated(self):
        scope = self.env["mcp.scope"].create({
            "name": "TEST write partners",
            "read_only": False,
            "require_approval": True,
            "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "can_read": True, "can_create": True})],
        })
        admin = self.env.ref("base.user_admin")
        res = self._call(scope, "create_record", {
            "model": "res.partner", "values": {"name": "AI Draft Co"}},
            user=admin)
        payload = self._payload(res)
        self.assertTrue(payload.get("approval_required"))
        req = self.env["mcp.approval.request"].browse(payload["approval_id"])
        self.assertEqual(req.state, "pending")
        # No partner created yet.
        self.assertFalse(self.env["res.partner"].search_count(
            [("name", "=", "AI Draft Co")]))
        # Approval executes it.
        req.action_approve()
        self.assertEqual(req.state, "executed")
        self.assertTrue(self.env["res.partner"].search_count(
            [("name", "=", "AI Draft Co")]))

    # -------------------------------------------------------------- rate limit
    def test_rate_limit_enforced(self):
        scope = self.env["mcp.scope"].create({
            "name": "TEST limited",
            "read_only": True,
            "rate_limit_per_hour": 2,
            "line_ids": [(0, 0, {
                "model_id": self.partner_model.id, "can_read": True})],
        })
        key = self._key(scope)
        ctx = {"api_key_id": key.id}
        self.assertFalse(self._call(scope, "count_records",
                                    {"model": "res.partner"}, ctx=ctx)["isError"])
        self.assertFalse(self._call(scope, "count_records",
                                    {"model": "res.partner"}, ctx=ctx)["isError"])
        third = self._call(scope, "count_records", {"model": "res.partner"}, ctx=ctx)
        self.assertTrue(third["isError"])
        self.assertIn("Rate limit", self._payload(third)["message"])

    # ------------------------------------------------------------------ audit
    def test_every_call_is_audited(self):
        before = self.env["mcp.audit.log"].search_count([])
        self._call(self.read_scope, "count_records", {"model": "res.partner"})
        after = self.env["mcp.audit.log"].search_count([])
        self.assertEqual(after, before + 1)

    # ------------------------------------------------------------ truncation
    def _capped_scope(self, cap):
        return self.env["mcp.scope"].create({
            "name": "TEST capped %s" % cap,
            "read_only": True,
            "rate_limit_per_hour": 0,
            "max_records": cap,
            "line_ids": [(0, 0, {
                "model_id": self.partner_model.id, "can_read": True})],
        })

    def test_search_never_returns_more_than_the_cap(self):
        """Fetching limit+1 to detect truncation must not leak the extra row."""
        self.env["res.partner"].create(
            [{"name": "MCP Cap %s" % i} for i in range(3)])
        payload = self._payload(self._call(
            self._capped_scope(2), "search_records", {"model": "res.partner"}))
        self.assertEqual(len(payload["records"]), 2)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["limit"], 2)

    def test_search_says_when_rows_were_cut_off(self):
        """The whole point: a capped page must not read as a complete answer.

        Without has_more the assistant receives a full page, has nothing to
        tell it the page was full, and reports partial data as the total.
        """
        self.env["res.partner"].create(
            [{"name": "MCP More %s" % i} for i in range(3)])
        payload = self._payload(self._call(
            self._capped_scope(2), "search_records", {"model": "res.partner"}))
        self.assertTrue(payload["has_more"])

    def test_search_says_when_the_page_is_the_whole_answer(self):
        self.env["res.partner"].create({"name": "MCP Sole Match Qx"})
        payload = self._payload(self._call(self.read_scope, "search_records", {
            "model": "res.partner",
            "domain": "[('name','=','MCP Sole Match Qx')]"}))
        self.assertEqual(payload["count"], 1)
        self.assertFalse(payload["has_more"])

    def test_read_group_says_when_groups_were_cut_off(self):
        """Worse here than on a search: a short report still totals up."""
        self.env["res.partner"].create([
            {"name": "MCP Group Co", "is_company": True},
            {"name": "MCP Group Person", "is_company": False}])
        payload = self._payload(self._call(
            self._capped_scope(1), "read_group",
            {"model": "res.partner", "group_by": ["is_company"]}))
        self.assertEqual(len(payload["groups"]), 1)
        self.assertTrue(payload["has_more"])

    def test_name_search_actually_runs(self):
        """Odoo 19 renamed name_search's domain parameter and kept no alias,
        so the old spelling failed with an opaque internal error on every
        call - on the one tool the business context tells the AI to use
        before filtering by any name."""
        res = self._call(self.read_scope, "name_search",
                         {"model": "res.partner", "name": "a"})
        self.assertFalse(res["isError"], self._payload(res))
        self.assertIn("results", self._payload(res))

    def test_name_search_applies_the_scope_record_domain(self):
        scope = self.env["mcp.scope"].create({
            "name": "TEST name_search domain", "read_only": True,
            "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {
                "model_id": self.partner_model.id, "can_read": True,
                "record_domain": "[('is_company','=',True)]"})],
        })
        self.env["res.partner"].create(
            {"name": "MCP Domain Person", "is_company": False})
        payload = self._payload(self._call(
            scope, "name_search",
            {"model": "res.partner", "name": "MCP Domain Person"}))
        self.assertEqual(payload["results"], [],
                         "the scope domain must narrow name_search too")

    def test_name_search_says_when_matches_were_cut_off(self):
        """A truncated match list is how an assistant picks the wrong Acme."""
        self.env["res.partner"].create(
            [{"name": "MCP Namesearch %s" % i} for i in range(3)])
        payload = self._payload(self._call(
            self._capped_scope(2), "name_search",
            {"model": "res.partner", "name": "MCP Namesearch"}))
        self.assertEqual(len(payload["results"]), 2)
        self.assertTrue(payload["has_more"])

    # ------------------------------------------------------------ list_models
    def test_list_models_hides_archived_rows(self):
        """An archived row is refused at call time, so advertising it lies."""
        scope = self.env["mcp.scope"].create({
            "name": "TEST archived rows", "read_only": True,
            "rate_limit_per_hour": 0})
        scope.add_models(["res.partner", "res.country"])
        scope.line_ids.filtered(
            lambda l: l.model_name == "res.country").active = False
        payload = self._payload(self._call(scope, "list_models", {}))
        names = {m["model"] for m in payload["models"]}
        self.assertIn("res.partner", names)
        self.assertNotIn("res.country", names)

    def test_list_models_reports_the_method_allow_list(self):
        """Guessing a method name and being refused is a wasted round trip."""
        scope = self.env["mcp.scope"].create({
            "name": "TEST methods listed", "read_only": False,
            "require_approval": False, "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {
                "model_id": self.partner_model.id, "can_read": True,
                "can_call_methods": True,
                "allowed_methods": "action_archive"})],
        })
        row = self._payload(self._call(scope, "list_models", {}))["models"][0]
        self.assertTrue(row["call_methods"])
        self.assertEqual(row["allowed_methods"], ["action_archive"])

    # ------------------------------------------------------------ tools/list
    def test_tools_carry_behaviour_annotations(self):
        """Clients use these to decide when to put a human in the loop."""
        tools = {t["name"]: t["annotations"]
                 for t in self.Engine.list_tools(self._write_scope())}
        self.assertTrue(tools["search_records"]["readOnlyHint"])
        # Meaningful only when readOnlyHint is false, so absent on read tools.
        self.assertNotIn("destructiveHint", tools["search_records"])
        self.assertFalse(tools["create_record"]["readOnlyHint"])
        self.assertFalse(tools["create_record"]["destructiveHint"],
                         "creating is additive, not destructive")
        self.assertTrue(tools["unlink_record"]["destructiveHint"])
        self.assertTrue(tools["write_record"]["idempotentHint"])
        for name, annotations in tools.items():
            self.assertFalse(annotations["openWorldHint"],
                             "%s acts on this database and nothing else" % name)

    def test_read_tool_descriptions_name_this_scopes_models(self):
        """So the assistant can answer without a discovery round trip first."""
        tools = {t["name"]: t["description"]
                 for t in self.Engine.list_tools(self.read_scope)}
        self.assertIn("res.partner", tools["search_records"])
        self.assertIn("res.partner", tools["read_group"])
        self.assertNotIn("res.partner", tools["get_schema"],
                         "only the model-parameterised read verbs carry it")

    def test_tools_list_is_deterministic(self):
        """MCP 2026-07-28 asks for a stable order so clients can cache it."""
        first = self.Engine.list_tools(self.read_scope)
        second = self.Engine.list_tools(self.read_scope)
        self.assertEqual([(t["name"], t["description"]) for t in first],
                         [(t["name"], t["description"]) for t in second])

    def test_list_capabilities_matches_tools_list(self):
        """Two places emit tool descriptions; they must not drift apart."""
        tools = {t["name"]: t["description"]
                 for t in self.Engine.list_tools(self.read_scope)}
        payload = self._payload(
            self._call(self.read_scope, "list_capabilities", {}))
        seen = 0
        for cap in payload["capabilities"]:
            for tool in cap["tools"]:
                self.assertEqual(tool["description"], tools[tool["name"]])
                seen += 1
        self.assertTrue(seen, "the read scope must advertise some tools")

    # ---------------------------------------------------------- approvals
    def test_approval_message_says_where_to_approve(self):
        """A queued approval with no destination sits untouched for a week."""
        scope = self.env["mcp.scope"].create({
            "name": "TEST approval message", "read_only": False,
            "require_approval": True, "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {
                "model_id": self.partner_model.id,
                "can_read": True, "can_create": True})],
        })
        payload = self._payload(self._call(
            scope, "create_record",
            {"model": "res.partner", "values": {"name": "MCP Queued Co"}},
            user=self.env.ref("base.user_admin")))
        self.assertTrue(payload["approval_required"])
        self.assertIn("Approvals", payload["message"])
        # Plain str, not a lazy translation: this travels in structuredContent.
        self.assertIsInstance(payload["message"], str)

    def test_the_requester_is_told_the_outcome(self):
        """They are the one person certain to be waiting on the answer."""
        request = self.env["mcp.approval.request"].create({
            "user_id": self.user.id,
            "scope_id": self.read_scope.id,
            "operation": "create",
            "model_name": "res.partner",
        })
        request._notify_approvers()
        self.assertIn(self.user.partner_id, request.message_partner_ids)
