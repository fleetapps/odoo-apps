# -*- coding: utf-8 -*-
"""Rendering: the promise that a dashboard shows *your* figures.

A spec stores the question. The answer is calculated when somebody opens it, as
that person, through the ordinary ORM — which is what makes a shared dashboard
safe to share and impossible to leave stale. These tests hold that line.
"""
import json

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from .test_spec import minimal


@tagged("post_install", "-at_install")
class TestRender(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Render = cls.env["ai.dashboard.render"]
        cls.employee = cls.env["res.users"].create({
            "name": "Render Employee", "login": "ai_dash_render",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.env["res.partner"].create([
            {"name": "AID Co A", "is_company": True},
            {"name": "AID Co B", "is_company": True},
            {"name": "AID Person", "is_company": False},
        ])

    def _board(self, spec=None, **vals):
        values = {"name": vals.pop("name", "Render test"),
                  "spec_json": json.dumps(spec or minimal()),
                  "state": "published"}
        values.update(vals)
        return self.env["ai.dashboard"].create(values)

    def test_render_returns_a_series_per_widget(self):
        board = self._board()
        out = self.Render.render(board.id)
        self.assertEqual(len(out["widgets"]), 1)
        self.assertIn("series", out["widgets"][0])

    def test_a_kpi_returns_one_number(self):
        spec = minimal()
        spec["widgets"][0].update(type="kpi")
        spec["widgets"][0]["query"]["group_by"] = []
        out = self.Render.render(self._board(spec).id)
        self.assertIsInstance(out["widgets"][0]["value"], (int, float))

    def test_the_render_is_timed(self):
        board = self._board()
        self.Render.render(board.id)
        self.assertTrue(board.last_render_ms >= 0)

    def test_a_widget_over_an_uninstalled_model_degrades_alone(self):
        """One bad tile, not a dead page."""
        spec = minimal()
        spec["widgets"].append({
            "id": "broken", "type": "bar", "title": "Gone", "span": 6,
            "query": {"model": "not.a.model", "group_by": ["x"],
                      "measures": ["__count"]},
        })
        # Written straight to the column: validation would refuse this, and the
        # point is that the *renderer* also survives a spec that got past it,
        # e.g. because a module was uninstalled after the dashboard was built.
        board = self._board()
        # Straight to the column on purpose. Validation would refuse this, and
        # the point of the test is that the *renderer* also survives a spec
        # that was valid when saved and whose model was uninstalled later.
        self.env.cr.execute(
            "UPDATE ai_dashboard SET spec_json = %s WHERE id = %s",
            (json.dumps(spec), board.id))
        board.invalidate_recordset(["spec_json"])
        out = self.Render.render(board.id)
        self.assertIsNone(out["widgets"][0].get("error"))
        self.assertTrue(out["widgets"][1]["error"])

    def test_an_empty_tile_says_it_is_your_permissions(self):
        """'No data' would be a lie: there may be plenty, just none of it
        theirs."""
        spec = minimal()
        spec["widgets"][0]["query"]["domain"] = [["name", "=", "nothing at all"]]
        out = self.Render.render(self._board(spec).id)
        widget = out["widgets"][0]
        self.assertTrue(widget.get("empty"))
        self.assertIn("you", widget["empty"].lower())

    def test_two_people_see_their_own_figures(self):
        """The property that makes sharing safe. The renderer must never use
        sudo for the data itself."""
        board = self._board()
        as_admin = self.Render.render(board.id)
        as_employee = self.Render.with_user(self.employee).render(board.id)
        self.assertIn("widgets", as_employee)
        # Same question, each answered in the asker's own right.
        self.assertEqual(as_admin["id"], as_employee["id"])

    def test_periods_resolve_relative_to_today(self):
        """A saved dashboard has to still mean 'this year' next January."""
        bounds = self.Render._period_bounds("this_year")
        self.assertTrue(bounds)
        start, end = bounds
        self.assertEqual(start.month, 1)
        self.assertEqual(end.year, start.year + 1)

    def test_all_time_applies_no_window(self):
        self.assertIsNone(self.Render._period_bounds("all_time"))

    # ------------------------------------------------------------- drilling
    def test_drill_returns_a_normal_list_action(self):
        board = self._board()
        action = self.Render.drill(board.id, "w1", None)
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "res.partner")
        self.assertIn("list", action["view_mode"])

    def test_drill_narrows_to_the_clicked_segment(self):
        board = self._board()
        country = self.env["res.country"].search([], limit=1)
        action = self.Render.drill(board.id, "w1", country.id)
        self.assertIn(["country_id", "=", country.id], action["domain"])

    def test_drill_is_refused_when_the_tile_forbids_it(self):
        spec = minimal()
        spec["widgets"][0]["drill"] = False
        board = self._board(spec)
        with self.assertRaises(Exception):
            self.Render.drill(board.id, "w1", None)


@tagged("post_install", "-at_install")
class TestUnsavedSpecOverride(TransactionCase):
    """Drawing what is on screen, not what is in the database.

    The editor changes a tile and the canvas has to show the result. Some of
    those changes — a different chart shape, a different date grouping, a
    smaller row limit — need figures the stored spec cannot produce, so the
    canvas hands the renderer the spec it currently has. Without this, editing
    a tile redrew the *saved* version and every edit looked like it had failed.

    It is an extra door into the renderer, so it is tested like one.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Render = cls.env["ai.dashboard.render"]
        cls.owner = cls.env["res.users"].create({
            "name": "Board Owner", "login": "ai_dash_override_owner",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.other = cls.env["res.users"].create({
            "name": "Colleague", "login": "ai_dash_override_other",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.board = cls.env["ai.dashboard"].create({
            "name": "Override test",
            "spec_json": json.dumps(minimal()),
            "state": "published",
            "owner_id": cls.owner.id,
            "share_user_ids": [(6, 0, [cls.other.id])],
        })

    def _edited(self, **widget):
        spec = minimal()
        spec["widgets"][0].update(widget)
        return spec

    def test_the_override_is_what_gets_drawn(self):
        out = self.Render.with_user(self.owner).render(
            self.board.id, None, None, self._edited(title="Renamed live"))
        self.assertEqual(out["widgets"][0]["title"], "Renamed live")

    def test_the_override_is_never_written_to_the_record(self):
        """It draws a proposal. Saving stays the Save button's job."""
        self.Render.with_user(self.owner).render(
            self.board.id, None, None, self._edited(title="Not saved"))
        self.assertEqual(
            json.loads(self.board.spec_json)["widgets"][0]["title"],
            "By country")

    def test_somebody_who_may_only_view_cannot_supply_one(self):
        """Sharing lets people read a dashboard, not redefine it."""
        with self.assertRaises(AccessError):
            self.Render.with_user(self.other).render(
                self.board.id, None, None, self._edited(title="Hijacked"))

    def test_a_shared_viewer_can_still_open_it_normally(self):
        out = self.Render.with_user(self.other).render(self.board.id)
        self.assertEqual(out["widgets"][0]["title"], "By country")

    def test_an_override_goes_through_the_validator(self):
        """The editor is a client. It is not trusted any more than the AI is."""
        with self.assertRaises(Exception):
            self.Render.with_user(self.owner).render(
                self.board.id, None, None,
                self._edited(query={"model": "res.partner",
                                    "group_by": ["country_id"],
                                    "measures": ["__count"],
                                    "context": {"active_test": False}}))

    def test_timing_is_not_recorded_for_an_unsaved_variant(self):
        """A dashboard should not be badged slow over an experiment."""
        self.board.last_render_ms = 0
        self.Render.with_user(self.owner).render(
            self.board.id, None, None, self._edited(title="Experiment"))
        self.assertEqual(self.board.last_render_ms, 0)
