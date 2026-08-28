# -*- coding: utf-8 -*-
"""Comparison against a previous period.

The bug this rewrite fixed is the interesting part: comparison used to read
the widget's *raw* domain, which never contains the date window a filter
applies — so on every dashboard the schema example produces, the trend arrow
silently never appeared. Nothing failed; it just was not there, which is the
hardest kind of absence to notice.
"""
import json

from odoo.tests import TransactionCase, tagged

from ..models import ai_dashboard_spec as spec_lib
from .test_spec import minimal


def dated(**overrides):
    """A spec whose period comes from a filter, as a real one does."""
    spec = minimal()
    spec["filters"] = [{"key": "period", "type": "date_range",
                        "label": "Period", "default": "this_year",
                        "field": "create_date"}]
    spec.update(overrides)
    return spec


@tagged("post_install", "-at_install")
class TestCompareSpec(TransactionCase):

    def test_a_dashboard_can_carry_a_comparison(self):
        out = spec_lib.validate(dated(compare={"to": "previous_year"}))
        self.assertEqual(out["compare"]["to"], "previous_year")

    def test_no_comparison_is_the_default(self):
        self.assertEqual(spec_lib.validate(minimal())["compare"]["to"], "none")

    def test_an_unknown_target_is_refused(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(dated(compare={"to": "last_fortnight"}))

    def test_a_widget_may_opt_out(self):
        spec = dated(compare={"to": "previous_year"})
        spec["widgets"][0]["compare"] = {"to": "none"}
        out = spec_lib.validate(spec)
        self.assertEqual(out["widgets"][0]["compare"]["to"], "none")

    def test_a_pie_cannot_carry_a_comparison(self):
        """Two periods in one pie is not a chart anybody can read."""
        spec = dated()
        spec["widgets"][0].update(type="pie", compare={"to": "previous_year"})
        with self.assertRaises(spec_lib.SpecError) as caught:
            spec_lib.validate(spec)
        self.assertIn("pie", str(caught.exception))

    def test_a_pie_is_fine_when_it_opts_out(self):
        spec = dated(compare={"to": "previous_year"})
        spec["widgets"][0].update(type="pie", compare={"to": "none"})
        spec_lib.validate(spec)  # must not raise


@tagged("post_install", "-at_install")
class TestCompareRender(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Render = cls.env["ai.dashboard.render"]
        cls.env["res.partner"].create([
            {"name": "AID Cmp A", "is_company": True},
            {"name": "AID Cmp B", "is_company": False},
        ])

    def _board(self, spec):
        return self.env["ai.dashboard"].create({
            "name": "Compare board", "spec_json": json.dumps(spec),
            "state": "published"})

    def _kpi_spec(self, **overrides):
        spec = dated(**overrides)
        spec["widgets"] = [{
            "id": "k", "type": "kpi", "title": "Contacts", "span": 3,
            "query": {"model": "res.partner", "measures": ["__count"]},
        }]
        return spec

    # ------------------------------------------------------------ the fix
    def test_a_filter_supplied_window_is_compared(self):
        """The regression. The date comes from the filter, not the domain —
        which is the case the old code could not see at all."""
        board = self._board(self._kpi_spec(compare={"to": "previous_year"}))
        widget = self.Render.render(board.id)["widgets"][0]
        self.assertIn("compare", widget,
                      "a filter-supplied period must still be comparable")
        self.assertEqual(widget["compare_mode"], "previous_year")

    def test_without_a_date_range_it_says_so(self):
        """Silence would read as "no change", which is a different claim."""
        spec = self._kpi_spec(compare={"to": "previous_year"})
        spec["filters"] = []
        widget = self.Render.render(self._board(spec).id)["widgets"][0]
        self.assertNotIn("compare", widget)
        self.assertTrue(widget.get("compare_note"))

    # ------------------------------------------------------- dashboard wide
    def test_the_viewer_can_turn_comparison_on(self):
        board = self._board(self._kpi_spec())
        plain = self.Render.render(board.id)["widgets"][0]
        self.assertNotIn("compare", plain)

        compared = self.Render.render(
            board.id, {"__compare": "previous_year"})["widgets"][0]
        self.assertIn("compare", compared)

    def test_the_viewer_overrides_the_spec(self):
        board = self._board(self._kpi_spec(compare={"to": "previous_year"}))
        out = self.Render.render(board.id, {"__compare": "none"})
        self.assertEqual(out["compare"], "none")
        self.assertNotIn("compare", out["widgets"][0])

    def test_a_widget_opting_out_stays_out(self):
        spec = self._kpi_spec(compare={"to": "previous_year"})
        spec["widgets"][0]["compare"] = {"to": "none"}
        widget = self.Render.render(self._board(spec).id)["widgets"][0]
        self.assertNotIn("compare", widget)

    def test_charts_get_an_aligned_comparison_series(self):
        spec = dated(compare={"to": "previous_year"})
        spec["widgets"] = [{
            "id": "b", "type": "bar", "title": "By type", "span": 6,
            "query": {"model": "res.partner", "group_by": ["is_company"],
                      "measures": ["__count"]},
        }]
        widget = self.Render.render(self._board(spec).id)["widgets"][0]
        self.assertIn("compare_series", widget)
        self.assertIsInstance(widget["compare_series"], list)

    def test_a_table_never_gets_one(self):
        """Rows do not line up across windows, so a comparison column would be
        confidently wrong."""
        spec = dated(compare={"to": "previous_year"})
        spec["widgets"] = [{
            "id": "t", "type": "table", "title": "Rows", "span": 12,
            "query": {"model": "res.partner", "group_by": ["is_company"],
                      "measures": ["__count"]},
        }]
        widget = self.Render.render(self._board(spec).id)["widgets"][0]
        self.assertNotIn("compare_series", widget)

    # -------------------------------------------------------- window shift
    def test_previous_year_shifts_by_a_year(self):
        domain = [["state", "=", "x"],
                  ["date", ">=", "2026-01-01"], ["date", "<", "2027-01-01"]]
        out = self.Render._shift_window(domain, "previous_year")
        self.assertIn(["date", ">=", "2025-01-01"], out)
        self.assertIn(["date", "<", "2026-01-01"], out)
        self.assertIn(["state", "=", "x"], out, "other conditions survive")

    def test_previous_period_shifts_by_the_span(self):
        domain = [["date", ">=", "2026-03-01"], ["date", "<", "2026-04-01"]]
        out = self.Render._shift_window(domain, "previous_period")
        self.assertIn(["date", ">=", "2026-02-01"], out)
        self.assertIn(["date", "<", "2026-03-01"], out)

    def test_the_shift_preserves_operator_arity(self):
        """Removing a condition from a domain with prefix operators would
        leave one short and produce something the ORM cannot parse."""
        domain = ["|", ["a", "=", 1], ["b", "=", 2],
                  ["date", ">=", "2026-01-01"], ["date", "<", "2027-01-01"]]
        out = self.Render._shift_window(domain, "previous_year")
        leaves = lambda d: sum(1 for t in d if not isinstance(t, str))
        self.assertEqual(leaves(domain), leaves(out))

    def test_an_ambiguous_window_declines_rather_than_guessing(self):
        domain = [["date", ">=", "2026-01-01"], ["date", ">=", "2026-06-01"],
                  ["date", "<", "2027-01-01"]]
        self.assertIsNone(self.Render._shift_window(domain, "previous_year"))

    def test_no_window_declines(self):
        self.assertIsNone(
            self.Render._shift_window([["state", "=", "x"]], "previous_year"))
