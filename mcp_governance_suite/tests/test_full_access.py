# -*- coding: utf-8 -*-
"""Full Access scopes: every model and every public method, with no matrix.

What it opens is the whole of what the signed-in user can do, so the tests
that matter most here are the ones that show what it still does not open: a
user's own Odoo rights, Odoo's own private-method rule, the Read Only kill
switch and the approval gate.
"""
import json

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFullAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["mcp.engine"]
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        group_user = cls.env.ref("base.group_user")
        cls.admin = cls.env["res.users"].create({
            "name": "MCP Full Admin", "login": "mcp_full_admin",
            "group_ids": [(6, 0, [group_user.id,
                                  cls.env.ref("base.group_system").id])],
        })
        cls.employee = cls.env["res.users"].create({
            "name": "MCP Full Employee", "login": "mcp_full_employee",
            "group_ids": [(6, 0, [group_user.id])],
        })
        cls.full = cls.env["mcp.scope"].create({
            "name": "TEST full access", "full_access": True,
            "read_only": False, "require_approval": False,
            "rate_limit_per_hour": 0,
        })
        cls.partner = cls.env["res.partner"].create({"name": "MCP Full Target"})

    def _call(self, name, args, user=None, scope=None):
        return self.Engine.with_user(user or self.admin).call_tool(
            scope or self.full, name, args, {})

    def _payload(self, result):
        return json.loads(result["content"][0]["text"])

    def _ok(self, name, args, **kw):
        result = self._call(name, args, **kw)
        self.assertFalse(result["isError"], self._payload(result))
        return self._payload(result)

    # ---------------------------------------------------------------- shape
    def test_the_shipped_scope_is_full_access_and_never_the_default(self):
        shipped = self.env.ref("mcp_governance_suite.scope_full_access")
        self.assertTrue(shipped.full_access)
        self.assertFalse(shipped.read_only)
        default = self.env["ir.config_parameter"].sudo().get_param(
            "mcp_governance_suite.default_scope_id")
        self.assertNotEqual(str(shipped.id), default)

    def test_every_model_is_reachable_with_no_matrix_row(self):
        self.assertFalse(self.full.line_ids)
        payload = self._ok("search_records", {"model": "res.users", "limit": 3})
        self.assertTrue(payload["records"])

    def test_list_models_says_so_once(self):
        payload = self._ok("list_models", {})
        self.assertTrue(payload["full_access"])
        names = {row["model"] for row in payload["models"]}
        self.assertIn("res.users", names)
        self.assertIn("ir.actions.server", names)

    def test_tool_descriptions_say_so(self):
        tools = {t["name"]: t["description"]
                 for t in self.Engine.list_tools(self.full)}
        self.assertIn("full access", tools["search_records"])

    # -------------------------------------------------------------- writing
    def test_create_update_and_delete_on_any_model(self):
        created = self._ok("create_record", {
            "model": "res.partner.category", "values": {"name": "MCP tag"}})
        tag = self.env["res.partner.category"].browse(created["id"])
        self._ok("write_record", {"model": "res.partner.category",
                                  "record_id": tag.id,
                                  "values": {"name": "MCP tag 2"}})
        self.assertEqual(tag.name, "MCP tag 2")
        self._ok("unlink_record", {"model": "res.partner.category",
                                   "record_id": tag.id})
        self.assertFalse(tag.exists())

    def test_a_server_action_can_be_built_and_run(self):
        """The allow-list's guard refuses `run`; Full Access follows Odoo's
        external API instead, which lets an administrator run an action."""
        created = self._ok("create_record", {
            "model": "ir.actions.server", "values": {
                "name": "MCP full action", "model_id": self.partner_model.id,
                "state": "code",
                "code": "env['res.partner'].browse(%d).write("
                        "{'comment': 'ran by MCP'})" % self.partner.id}})
        self._ok("call_method", {"model": "ir.actions.server", "method": "run",
                                 "record_ids": [created["id"]]})
        self.assertIn("ran by MCP", str(self.partner.comment))

    def test_a_scheduled_action_can_be_created(self):
        created = self._ok("create_record", {
            "model": "ir.cron", "values": {
                "name": "MCP full cron", "model_id": self.partner_model.id,
                "state": "code", "code": "pass",
                "interval_number": 1, "interval_type": "days"}})
        self.assertTrue(self.env["ir.cron"].browse(created["id"]).exists())

    # ------------------------------------------------- what it still refuses
    def test_odoos_own_private_methods_stay_refused(self):
        for method in ("sudo", "with_user", "_compute_display_name"):
            result = self._call("call_method", {
                "model": "res.partner", "method": method,
                "record_ids": [self.partner.id]})
            self.assertTrue(result["isError"], method)
            self.assertEqual(self._payload(result)["error"], "AccessError")

    def test_a_method_that_does_not_exist_says_so(self):
        result = self._call("call_method", {
            "model": "res.partner", "method": "no_such_method",
            "record_ids": [self.partner.id]})
        self.assertTrue(result["isError"])
        self.assertIn("no_such_method", self._payload(result)["message"])

    def test_it_never_exceeds_the_users_own_rights(self):
        """An employee on Full Access is still an employee."""
        result = self._call("create_record", {
            "model": "ir.actions.server", "values": {
                "name": "MCP denied", "model_id": self.partner_model.id,
                "state": "code", "code": "pass"}}, user=self.employee)
        self.assertTrue(result["isError"])
        self.assertEqual(self._payload(result)["error"], "AccessError")

    def test_read_only_still_switches_writing_off(self):
        self.full.read_only = True
        names = {t["name"] for t in self.Engine.list_tools(self.full)}
        self.assertNotIn("create_record", names)
        result = self._call("create_record", {
            "model": "res.partner.category", "values": {"name": "MCP no"}})
        self.assertTrue(result["isError"])

    def test_the_approval_gate_still_holds_and_then_executes(self):
        self.full.require_approval = True
        payload = self._ok("call_method", {
            "model": "res.partner", "method": "action_archive",
            "record_ids": [self.partner.id]})
        self.assertTrue(payload["approval_required"])
        self.assertTrue(self.partner.active, "nothing happens before approval")
        request = self.env["mcp.approval.request"].browse(payload["approval_id"])
        request.action_approve()
        self.assertEqual(request.state, "executed")
        self.assertFalse(self.partner.active)
