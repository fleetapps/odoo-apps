# -*- coding: utf-8 -*-
"""Permission matrix, method allow-list and the bulk model picker."""
import json

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestModelPermissions(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Engine = cls.env["mcp.engine"]
        cls.partner_model = cls.env["ir.model"]._get("res.partner")
        cls.user = cls.env["res.users"].create({
            "name": "MCP Perm User",
            "login": "mcp_perm_user",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.scope = cls.env["mcp.scope"].create({
            "name": "TEST method scope",
            "read_only": False,
            "require_approval": False,
            "rate_limit_per_hour": 0,
        })
        cls.line = cls.env["mcp.scope.line"].create({
            "scope_id": cls.scope.id,
            "model_id": cls.partner_model.id,
            "can_read": True,
        })

    def _call(self, name, args):
        return self.Engine.with_user(self.user).call_tool(
            self.scope, name, args, {})

    def _payload(self, result):
        return json.loads(result["content"][0]["text"])

    # ------------------------------------------------------- allow-list guard
    def test_private_methods_are_rejected_at_save(self):
        with self.assertRaises(ValidationError):
            self.line.write({"can_call_methods": True,
                             "allowed_methods": "_compute_display_name"})

    def test_raw_orm_verbs_are_rejected_at_save(self):
        """Blocking these forces callers through the audited write tools."""
        for method in ("write", "unlink", "sudo", "search"):
            with self.assertRaises(ValidationError, msg=method):
                self.line.write({"can_call_methods": True,
                                 "allowed_methods": method})

    def test_nonexistent_method_is_rejected_at_save(self):
        with self.assertRaises(ValidationError):
            self.line.write({"can_call_methods": True,
                             "allowed_methods": "action_does_not_exist"})

    def test_valid_method_saves(self):
        self.line.write({"can_call_methods": True,
                         "allowed_methods": "action_archive"})
        self.assertEqual(self.line.allowed_method_set(), {"action_archive"})

    # --------------------------------------------------------- engine gating
    def test_method_call_denied_without_the_matrix_bit(self):
        res = self._call("call_method", {
            "model": "res.partner", "method": "action_archive",
            "record_ids": []})
        self.assertTrue(res["isError"])
        self.assertIn("Method Calls", self._payload(res)["message"])

    def test_empty_allow_list_permits_nothing(self):
        """The bit alone must not be enough - the list is the real gate."""
        self.line.can_call_methods = True
        res = self._call("call_method", {
            "model": "res.partner", "method": "action_archive",
            "record_ids": []})
        self.assertTrue(res["isError"])
        self.assertIn("no method names", self._payload(res)["message"])

    def test_method_outside_allow_list_is_refused(self):
        self.line.write({"can_call_methods": True,
                         "allowed_methods": "action_archive"})
        res = self._call("call_method", {
            "model": "res.partner", "method": "action_unarchive",
            "record_ids": []})
        self.assertTrue(res["isError"])
        self.assertIn("not allow-listed", self._payload(res)["message"])

    def test_allow_listed_method_runs(self):
        partner = self.env["res.partner"].create({"name": "TEST archive me"})
        self.line.write({"can_call_methods": True,
                         "allowed_methods": "action_archive"})
        res = self._call("call_method", {
            "model": "res.partner", "method": "action_archive",
            "record_ids": [partner.id]})
        self.assertFalse(res["isError"], self._payload(res))
        self.assertFalse(partner.active)

    def test_method_call_is_a_write_tool(self):
        """So read-only scopes and odoo:read tokens never see or reach it."""
        tool = self.env.ref("mcp_governance_suite.tool_call_method")
        self.assertTrue(tool.writes)

    def test_method_call_respects_record_domain(self):
        self.line.write({"can_call_methods": True,
                         "allowed_methods": "action_archive",
                         "record_domain": "[('is_company', '=', True)]"})
        person = self.env["res.partner"].create(
            {"name": "TEST person", "is_company": False})
        res = self._call("call_method", {
            "model": "res.partner", "method": "action_archive",
            "record_ids": [person.id]})
        self.assertTrue(res["isError"])
        self.assertIn("outside this scope", self._payload(res)["message"])

    # ------------------------------------------------------ actionable errors
    def test_missing_model_error_names_the_model_and_the_fix(self):
        """Opaque 'access denied' is the top complaint about ERP MCP servers."""
        res = self._call("search_records", {"model": "res.currency"})
        self.assertTrue(res["isError"])
        message = self._payload(res)["message"]
        self.assertIn("res.currency", message)
        self.assertIn("Model Permissions", message)

    def test_denied_operation_error_names_the_switch(self):
        res = self._call("create_record", {
            "model": "res.partner", "values": {"name": "x"}})
        self.assertTrue(res["isError"])
        self.assertIn("Create", self._payload(res)["message"])


@tagged("post_install", "-at_install")
class TestModelPicker(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scope = cls.env["mcp.scope"].create({"name": "TEST picker scope"})
        cls.models = cls.env["ir.model"].search(
            [("model", "in", ["res.partner", "res.company", "res.country"])])

    def _picker(self, **kw):
        vals = {"scope_id": self.scope.id, "model_ids": [(6, 0, self.models.ids)]}
        vals.update(kw)
        return self.env["mcp.model.picker"].create(vals)

    def test_adds_all_selected_models(self):
        self._picker().action_add()
        self.assertEqual(len(self.scope.line_ids), 3)

    def test_read_preset_is_read_only(self):
        self._picker(preset="read").action_add()
        line = self.scope.line_ids[0]
        self.assertTrue(line.can_read)
        self.assertFalse(line.can_create or line.can_write or line.can_unlink)

    def test_full_preset_grants_delete(self):
        self._picker(preset="full").action_add()
        self.assertTrue(all(self.scope.line_ids.mapped("can_unlink")))

    def test_rerunning_skips_existing_models(self):
        """Safe to re-run: a second pass must not duplicate or raise."""
        self._picker().action_add()
        self._picker().action_add()
        self.assertEqual(len(self.scope.line_ids), 3)

    def test_archived_rows_still_count_as_existing(self):
        """The unique constraint is in the database and ignores archiving, so a
        re-run must not try to insert over an archived row."""
        self._picker().action_add()
        self.scope.line_ids[0].active = False
        picker = self._picker()
        self.assertEqual(picker.new_count, 0)
        picker.action_add()  # must not raise a constraint violation
        self.assertEqual(len(self.scope.with_context(
            active_test=False).line_ids), 3)

    def test_preview_counts_are_accurate(self):
        picker = self._picker()
        self.assertEqual(picker.new_count, 3)
        self.assertEqual(picker.already_count, 0)
        picker.action_add()
        again = self._picker()
        self.assertEqual(again.new_count, 0)
        self.assertEqual(again.already_count, 3)
