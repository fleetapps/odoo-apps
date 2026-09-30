# -*- coding: utf-8 -*-
"""History and revert.

An assistant that can edit your reports needs an undo, and the history has to
be an account of what happened rather than something that can be rewritten —
so restoring writes a new version instead of moving a pointer.
"""
import json

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .test_spec import minimal


@tagged("post_install", "-at_install")
class TestVersions(TransactionCase):

    def setUp(self):
        super().setUp()
        self.board = self.env["ai.dashboard"].create({
            "name": "Versioned",
            "spec_json": json.dumps(minimal(title="First")),
        })

    def test_creating_records_the_first_version(self):
        self.assertEqual(self.board.version_count, 1)

    def test_every_spec_change_adds_a_version(self):
        self.board.write({"spec_json": json.dumps(minimal(title="Second"))})
        self.assertEqual(self.board.version_count, 2)

    def test_changing_only_the_name_does_not_add_a_version(self):
        self.board.write({"name": "Renamed"})
        self.assertEqual(self.board.version_count, 1)

    def test_restoring_returns_the_earlier_spec(self):
        original = self.board.spec_json
        self.board.write({"spec_json": json.dumps(minimal(title="Second"))})
        first = self.board.version_ids.sorted("id")[0]
        first.action_restore()
        self.assertEqual(json.loads(self.board.spec_json),
                         json.loads(original))

    def test_restoring_appends_rather_than_rewrites(self):
        self.board.write({"spec_json": json.dumps(minimal(title="Second"))})
        before = self.board.version_count
        self.board.version_ids.sorted("id")[0].action_restore()
        self.assertEqual(self.board.version_count, before + 1,
                         "history is append-only")

    def test_history_cannot_be_edited(self):
        with self.assertRaises(UserError):
            self.board.version_ids[0].write({"note": "rewritten"})

    def test_the_note_explains_what_changed(self):
        self.board.write({
            "spec_json": json.dumps(minimal(title="Second")),
            "_version_note": "added last-year comparison",
        })
        latest = self.board.version_ids.sorted("id")[-1]
        self.assertEqual(latest.note, "added last-year comparison")
