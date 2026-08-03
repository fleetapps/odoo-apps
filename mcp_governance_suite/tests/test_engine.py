# -*- coding: utf-8 -*-
"""Engine governance tests: scoping, blacklists, read-only, approval, limits.

Odoo testing reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/testing.html
"""
import json

from odoo.tests import TransactionCase, tagged


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
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
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
        res = self._call(self.read_scope, "search_records", {"model": "res.users"})
        self.assertTrue(res["isError"])
        self.assertIn("denies", self._payload(res)["message"])

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
