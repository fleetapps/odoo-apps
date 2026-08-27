# -*- coding: utf-8 -*-
"""The MCP tools, and the governance they inherit.

The most important test in this file is test_write_tools_are_hidden_from_a
_read_only_scope. A writing handler left out of mcp.tool._write_handlers()
computes writes=False, so it is advertised to read-only connections and
executes for tokens that were never granted odoo:write — the one way this
module could quietly punch through the layer it is built on.
"""
import json

from odoo.tests import TransactionCase, tagged

from ..models.mcp_engine import (
    DASHBOARD_READ_HANDLERS,
    DASHBOARD_WRITE_HANDLERS,
)
from .test_spec import minimal


@tagged("post_install", "-at_install")
class TestDashboardTools(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["mcp.engine"]
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.user = cls.env["res.users"].create({
            "name": "Tools User", "login": "ai_dash_tools",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.scope = cls.env["mcp.scope"].create({
            "name": "TEST dashboard tools",
            "read_only": False,
            "require_approval": False,
            "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {"model_id": cls.partner_model.id,
                                 "can_read": True})],
        })
        cls.read_only_scope = cls.env["mcp.scope"].create({
            "name": "TEST dashboard read only",
            "read_only": True,
            "rate_limit_per_hour": 0,
            "line_ids": [(0, 0, {"model_id": cls.partner_model.id,
                                 "can_read": True})],
        })

    def _call(self, name, args, scope=None, user=None):
        return self.Engine.with_user(user or self.env.user).call_tool(
            scope or self.scope, name, args, {})

    def _payload(self, result):
        return json.loads(result["content"][0]["text"])

    # ------------------------------------------------------- classification
    def test_write_tools_are_registered_as_writing(self):
        Tool = self.env["mcp.tool"]
        for handler in DASHBOARD_WRITE_HANDLERS:
            self.assertIn(handler, Tool._write_handlers(),
                          "%s writes and must be registered" % handler)

    def test_read_tools_are_not_registered_as_writing(self):
        Tool = self.env["mcp.tool"]
        for handler in DASHBOARD_READ_HANDLERS:
            self.assertNotIn(handler, Tool._write_handlers())

    def test_write_tools_are_hidden_from_a_read_only_scope(self):
        """The regression this module's extension point exists to prevent."""
        names = {t["name"] for t in self.Engine.list_tools(self.read_only_scope)}
        self.assertIn("get_dashboard_schema", names)
        self.assertNotIn("save_dashboard", names)
        self.assertNotIn("preview_dashboard", names)

    def test_write_tools_carry_the_right_annotations(self):
        tools = {t["name"]: t["annotations"]
                 for t in self.Engine.list_tools(self.scope)}
        self.assertTrue(tools["get_dashboard_schema"]["readOnlyHint"])
        self.assertFalse(tools["save_dashboard"]["readOnlyHint"])
        self.assertTrue(tools["delete_dashboard"]["destructiveHint"])

    def test_no_tool_can_reach_a_server_action(self):
        """ir.actions.server runs safe_eval(code, mode='exec'). Write access
        there is remote code execution, so it must be unreachable."""
        for tool in self.Engine.list_tools(self.scope):
            schema = json.dumps(tool.get("inputSchema", {}))
            self.assertNotIn("ir.actions.server", schema)
            self.assertNotIn("ir.actions.server", tool["description"])

    # ------------------------------------------------------------ discovery
    def test_the_schema_tool_teaches_the_format(self):
        payload = self._payload(self._call("get_dashboard_schema", {}))
        self.assertIn("widget_types", payload)
        self.assertIn("example", payload)
        self.assertIn("kpi", payload["widget_types"])

    def test_the_schema_example_is_itself_valid(self):
        """A worked example a model copies has to actually validate, or the
        first thing it does is get refused."""
        from ..models import ai_dashboard_spec as spec_lib
        example = self._payload(self._call("get_dashboard_schema", {}))["example"]
        spec_lib.validate(example)  # structure only; sale.order may be absent

    def test_seed_from_view_suggests_real_fields(self):
        payload = self._payload(
            self._call("seed_from_view", {"model": "res.partner"}))
        self.assertTrue(payload["suggested_group_by"])
        self.assertEqual(payload["model"], "res.partner")

    def test_seed_refuses_a_model_outside_the_scope(self):
        result = self._call("seed_from_view", {"model": "res.users"})
        self.assertTrue(result["isError"])

    # -------------------------------------------------------------- writing
    def test_preview_creates_a_draft_and_samples_the_figures(self):
        payload = self._payload(
            self._call("preview_dashboard", {"spec": minimal()}))
        self.assertEqual(payload["state"], "draft")
        self.assertTrue(payload["url"].endswith(str(payload["dashboard_id"])))
        self.assertIsInstance(payload["sample"], list)
        board = self.env["ai.dashboard"].browse(payload["dashboard_id"])
        self.assertEqual(board.state, "draft")
        self.assertTrue(board.built_by_ai)

    def test_a_preview_does_not_reach_the_app_tile(self):
        payload = self._payload(
            self._call("preview_dashboard", {"spec": minimal()}))
        published = self.env["ai.dashboard"].search(
            [("id", "=", payload["dashboard_id"]), ("state", "=", "published")])
        self.assertFalse(published)

    def test_save_publishes(self):
        payload = self._payload(
            self._call("save_dashboard", {"spec": minimal(), "name": "Saved"}))
        board = self.env["ai.dashboard"].browse(payload["dashboard_id"])
        self.assertEqual(board.state, "published")
        self.assertEqual(board.name, "Saved")

    def test_save_can_publish_an_existing_preview(self):
        preview = self._payload(
            self._call("preview_dashboard", {"spec": minimal()}))
        self._call("save_dashboard",
                   {"dashboard_id": preview["dashboard_id"]})
        board = self.env["ai.dashboard"].browse(preview["dashboard_id"])
        self.assertEqual(board.state, "published")

    def test_an_invalid_spec_comes_back_as_a_usable_error(self):
        """The correction loop: the model has to be able to act on this."""
        bad = minimal()
        bad["widgets"][0]["query"]["measures"] = ["name:sum"]
        result = self._call("preview_dashboard", {"spec": bad})
        self.assertTrue(result["isError"])
        self.assertIn("numeric", self._payload(result)["message"])

    def test_a_missing_spec_points_at_the_schema_tool(self):
        result = self._call("preview_dashboard", {})
        self.assertTrue(result["isError"])
        self.assertIn("get_dashboard_schema", self._payload(result)["message"])

    def test_get_dashboard_returns_the_spec_for_editing(self):
        created = self._payload(
            self._call("save_dashboard", {"spec": minimal()}))
        payload = self._payload(self._call(
            "get_dashboard", {"dashboard_id": created["dashboard_id"]}))
        self.assertEqual(payload["spec"]["schema"], minimal()["schema"])
        self.assertTrue(payload["explanation"])

    def test_deleting_someone_elses_dashboard_is_refused(self):
        board = self.env["ai.dashboard"].with_user(self.user).create({
            "name": "Theirs", "spec_json": json.dumps(minimal()),
            "state": "published",
        })
        result = self._call("delete_dashboard", {"dashboard_id": board.id})
        self.assertTrue(result["isError"])

    def test_every_call_is_audited(self):
        before = self.env["mcp.audit.log"].search_count([])
        self._call("get_dashboard_schema", {})
        self.assertEqual(self.env["mcp.audit.log"].search_count([]), before + 1)
