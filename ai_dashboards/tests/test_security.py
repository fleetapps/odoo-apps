# -*- coding: utf-8 -*-
"""Who can see, build and change a dashboard.

Sharing on this model means *sight*, never edit. That distinction is the one
worth testing hardest: without it, one person's change silently rewrites what a
whole team looks at every morning, and nobody finds out until the numbers are
wrong in a meeting.
"""
import json

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from .test_spec import minimal


@tagged("post_install", "-at_install")
class TestDashboardAccess(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alice = cls.env["res.users"].create({
            "name": "Alice", "login": "ai_dash_alice",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.bob = cls.env["res.users"].create({
            "name": "Bob", "login": "ai_dash_bob",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.team = cls.env["res.groups"].create({"name": "TEST dashboard team"})

    def _dashboard(self, user, **vals):
        values = {
            "name": vals.pop("name", "Alice's board"),
            "spec_json": json.dumps(minimal()),
            "state": "published",
        }
        values.update(vals)
        return self.env["ai.dashboard"].with_user(user).create(values)

    # ------------------------------------------------------------- the grant
    def test_every_mcp_user_can_build_dashboards(self):
        """Same reasoning as the MCP User role: navigation, not authority."""
        self.assertTrue(self.alice.has_group(
            "ai_dashboards.group_dashboard_user"))

    def test_an_employee_is_not_made_an_administrator(self):
        self.assertFalse(self.alice.has_group(
            "ai_dashboards.group_dashboard_admin"))

    # ---------------------------------------------------------- own vs others
    def test_a_private_dashboard_is_invisible_to_others(self):
        board = self._dashboard(self.alice)
        found = self.env["ai.dashboard"].with_user(self.bob).search(
            [("id", "=", board.id)])
        self.assertFalse(found, "a private dashboard must not be readable")

    def test_sharing_to_a_group_makes_it_visible(self):
        self.bob.write({"group_ids": [(4, self.team.id)]})
        board = self._dashboard(self.alice, group_ids=[(6, 0, [self.team.id])])
        found = self.env["ai.dashboard"].with_user(self.bob).search(
            [("id", "=", board.id)])
        self.assertTrue(found, "a shared dashboard must be readable")

    def test_sharing_grants_sight_and_not_edit(self):
        """The one that matters: a colleague can look, never rewrite."""
        self.bob.write({"group_ids": [(4, self.team.id)]})
        board = self._dashboard(self.alice, group_ids=[(6, 0, [self.team.id])])
        spec = minimal(title="Bob's version")
        with self.assertRaises(AccessError):
            board.with_user(self.bob).write({"spec_json": json.dumps(spec)})

    def test_a_colleague_can_duplicate_and_own_the_copy(self):
        self.bob.write({"group_ids": [(4, self.team.id)]})
        board = self._dashboard(self.alice, group_ids=[(6, 0, [self.team.id])])
        board.with_user(self.bob).action_duplicate()
        copy = self.env["ai.dashboard"].with_user(self.bob).search(
            [("owner_id", "=", self.bob.id)], limit=1)
        self.assertTrue(copy)
        self.assertEqual(copy.state, "draft")
        self.assertFalse(copy.group_ids, "a copy starts private")

    def test_only_an_owner_may_delete(self):
        self.bob.write({"group_ids": [(4, self.team.id)]})
        board = self._dashboard(self.alice, group_ids=[(6, 0, [self.team.id])])
        with self.assertRaises(AccessError):
            board.with_user(self.bob).unlink()

    def test_a_preview_is_private_even_when_shared(self):
        """A draft is nobody's business until its owner has looked at it."""
        self.bob.write({"group_ids": [(4, self.team.id)]})
        board = self._dashboard(self.alice, state="draft",
                                group_ids=[(6, 0, [self.team.id])])
        found = self.env["ai.dashboard"].with_user(self.bob).search(
            [("id", "=", board.id)])
        self.assertFalse(found)

    # ------------------------------------------------------------- pinning
    def test_pinning_is_administrator_only(self):
        board = self._dashboard(self.alice)
        with self.assertRaises(AccessError):
            board.with_user(self.alice).action_pin_to_menu()
