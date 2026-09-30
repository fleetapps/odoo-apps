# -*- coding: utf-8 -*-
"""Bulk model adding - the one path the wizard, the install hook and the
Connect screen all go through, so it has to be idempotent and unsurprising.
"""
from odoo.tests import TransactionCase, tagged

from ..models.mcp_scope import SUGGESTED_MODELS


@tagged("post_install", "-at_install")
class TestScopeAddModels(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scope = cls.env["mcp.scope"].create({
            "name": "TEST add models", "read_only": True})

    def test_absent_models_are_skipped_not_fatal(self):
        """The whole reason this is a hook and not a data file."""
        added = self.scope.add_models(
            ["res.partner", "res.country", "definitely.not.a.model"])
        self.assertEqual(set(added.mapped("model_name")),
                         {"res.partner", "res.country"})

    def test_running_it_twice_adds_nothing(self):
        self.scope.add_models(["res.partner"])
        self.assertFalse(self.scope.add_models(["res.partner"]))
        self.assertEqual(len(self.scope.line_ids), 1)

    def test_archived_rows_still_count_as_present(self):
        """The uniqueness constraint lives in the database, which does not
        know about archiving - reading line_ids alone would hit it."""
        line = self.scope.add_models(["res.partner"])
        line.active = False
        self.assertFalse(self.scope.add_models(["res.partner"]))

    def test_a_repeated_name_in_one_call_inserts_once(self):
        added = self.scope.add_models(["res.partner", "res.partner"])
        self.assertEqual(len(added), 1)

    def test_the_preset_sets_the_switches(self):
        line = self.scope.add_models(["res.partner"], preset="draft")
        self.assertTrue(line.can_read)
        self.assertTrue(line.can_create)
        self.assertTrue(line.can_write)
        self.assertFalse(line.can_unlink, "'draft' must never grant delete")

    def test_the_default_preset_is_read_only(self):
        line = self.scope.add_models(["res.partner"])
        self.assertTrue(line.can_read)
        self.assertFalse(any((line.can_create, line.can_write,
                              line.can_unlink, line.can_call_methods)))

    def test_the_suggested_set_is_safe_to_apply_to_any_database(self):
        added = self.scope.add_models(SUGGESTED_MODELS)
        for line in added:
            self.assertIn(line.model_name, SUGGESTED_MODELS)
        self.assertFalse(self.scope.add_models(SUGGESTED_MODELS),
                         "the Connect screen button must be re-runnable")

    def test_readable_model_names_are_sorted_and_unique(self):
        """Tool descriptions are built from this, and MCP asks for a stable
        order so clients can cache the tool list."""
        self.scope.add_models(["res.country", "res.partner"])
        self.assertEqual(self.scope.readable_model_names(),
                         ["res.country", "res.partner"])

    def test_readable_model_names_excludes_unreadable_rows(self):
        self.scope.add_models(["res.partner"])
        self.scope.line_ids.can_read = False
        self.assertEqual(self.scope.readable_model_names(), [])
