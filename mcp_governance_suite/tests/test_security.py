# -*- coding: utf-8 -*-
"""Who can see and use the app at all.

The module shipped for several versions with nothing implying group_mcp_user,
so the Odoo MCP menu was invisible to every internal user and the product's
whole promise was unreachable for anyone an administrator had not hand-picked.
These tests pin the fix down, and pin down that it granted navigation only.
"""
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEmployeeAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["res.users"].create({
            "name": "MCP Plain Employee",
            "login": "mcp_plain_employee",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })

    def test_every_employee_holds_the_mcp_user_role(self):
        """Nobody finds a group they were never told about."""
        self.assertTrue(self.employee.has_group(
            "mcp_governance_suite.group_mcp_user"))

    def test_an_employee_is_not_made_an_administrator(self):
        for group in ("group_mcp_approver", "group_mcp_admin"):
            self.assertFalse(
                self.employee.has_group("mcp_governance_suite.%s" % group),
                "%s must stay something an administrator grants" % group)

    def test_an_employee_can_open_the_connect_screen(self):
        state = self.env["mcp.connect"].with_user(self.employee).get_state()
        self.assertIn("urls", state)
        self.assertFalse(state["can_admin"])

    def test_an_employee_cannot_toggle_writes(self):
        """Navigation, not authority: the governance controls stay admin-only."""
        connect = self.env["mcp.connect"].with_user(self.employee)
        self.assertFalse(connect.get_state()["writes"]["can_toggle"])

    def test_an_employee_cannot_widen_the_matrix(self):
        connect = self.env["mcp.connect"].with_user(self.employee)
        with self.assertRaises(AccessError):
            connect.add_suggested_models()

    def test_an_employee_keys_only_their_own_governance_scope(self):
        """They may hold a key; they may not choose the rules it runs under.

        Otherwise anyone could bind a key to whatever permissive scope happens
        to exist and step around the approval gate set for them.
        """
        loose = self.env["mcp.scope"].create({
            "name": "TEST unapproved writes",
            "read_only": False, "require_approval": False})
        with self.assertRaises(ValidationError):
            self.env["mcp.api.key"].with_user(self.employee).create({
                "name": "TEST employee key",
                "user_id": self.employee.id,
                "scope_id": loose.id,
            })

    def test_an_employee_can_key_the_scope_they_are_governed_by(self):
        effective = self.employee.sudo().mcp_effective_scope()
        key = self.env["mcp.api.key"].with_user(self.employee).create({
            "name": "TEST own scope key",
            "user_id": self.employee.id,
            "scope_id": effective.id,
        })
        self.assertEqual(key.scope_id, effective)
        self.assertFalse(key.can_choose_scope)
