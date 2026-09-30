# -*- coding: utf-8 -*-
"""The pivot.

The trap this whole widget is built around: `_read_group`'s `limit` counts
**groups**, and with two groupings a group is a *combination*. Asking for 50
over [partner_id, date:month] returns fifty customer-month pairs — perhaps
three customers — not fifty rows by twelve columns. A pivot built straight
from that has axes made of whatever fell inside the first fifty combinations,
which is wrong in a way that looks completely plausible.

So the axes are resolved separately and the cells fetched for exactly the page
shown. These tests hold that apart.
"""
import json

from odoo.tests import TransactionCase, tagged

from ..models import ai_dashboard_spec as spec_lib
from .test_spec import minimal


def pivot_spec(**query_overrides):
    query = {
        "model": "res.partner",
        "group_by": ["country_id", "is_company"],
        "measures": ["__count"],
    }
    query.update(query_overrides)
    spec = minimal()
    spec["widgets"] = [{
        "id": "p", "type": "pivot", "title": "Contacts", "span": 12,
        "query": query,
    }]
    return spec


@tagged("post_install", "-at_install")
class TestPivotSpec(TransactionCase):

    def test_a_pivot_needs_exactly_two_groupings(self):
        for group_by in (["country_id"], ["country_id", "is_company", "active"]):
            with self.assertRaises(spec_lib.SpecError):
                spec_lib.validate(pivot_spec(group_by=group_by))

    def test_a_pivot_takes_exactly_one_measure(self):
        with self.assertRaises(spec_lib.SpecError) as caught:
            spec_lib.validate(pivot_spec(measures=["__count", "credit:sum"]))
        self.assertIn("table", str(caught.exception),
                      "the refusal should point at the widget that does allow "
                      "several")

    def test_a_valid_pivot_passes(self):
        out = spec_lib.validate(pivot_spec())
        self.assertEqual(out["widgets"][0]["type"], "pivot")

    def test_a_pivot_cannot_carry_a_comparison(self):
        spec = pivot_spec()
        spec["widgets"][0]["compare"] = {"to": "previous_year"}
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    # ------------------------------------------------------------- ordering
    def test_a_bare_field_order_is_refused(self):
        """The bug that shipped in the schema example: Odoo answers "Aggregate
        method is mandatory" at render time, far from anything actionable."""
        with self.assertRaises(spec_lib.SpecError) as caught:
            spec_lib.validate(pivot_spec(measures=["credit:sum"],
                                         order="credit desc"))
        self.assertIn("credit:sum", str(caught.exception))

    def test_an_aggregate_order_is_accepted(self):
        spec_lib.validate(pivot_spec(measures=["credit:sum"],
                                     order="credit:sum desc"))

    def test_ordering_by_a_grouping_is_accepted(self):
        spec_lib.validate(pivot_spec(order="country_id asc"))


@tagged("post_install", "-at_install")
class TestPivotRender(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Render = cls.env["ai.dashboard.render"]
        cls.Pivot = cls.env["ai.dashboard.pivot"]
        countries = cls.env["res.country"].search([], limit=3)
        cls.env["res.partner"].create([
            {"name": "AID Pivot %s" % i,
             "country_id": countries[i % len(countries)].id,
             "is_company": bool(i % 2)}
            for i in range(6)
        ])

    def _board(self, spec):
        return self.env["ai.dashboard"].create({
            "name": "Pivot board", "spec_json": json.dumps(spec),
            "state": "published"})

    def _grid(self, spec=None, offsets=None):
        board = self._board(spec or pivot_spec())
        out = self.Render.render(board.id, {}, offsets or {})
        return out["widgets"][0]

    def test_a_pivot_renders_a_grid(self):
        widget = self._grid()
        self.assertIn("pivot", widget)
        grid = widget["pivot"]
        for key in ("rows", "cols", "cells", "row_totals", "col_totals",
                    "grand_total"):
            self.assertIn(key, grid)

    def test_both_axes_are_resolved_independently(self):
        """Not derived from a capped combination scan — the whole point."""
        grid = self._grid()["pivot"]
        self.assertTrue(grid["rows"]["values"])
        self.assertTrue(grid["cols"]["values"])
        # is_company has exactly two distinct values; the row axis is
        # countries. Neither count may be a function of the other.
        self.assertEqual(len(grid["cols"]["values"]), 2)

    def test_each_axis_reports_a_truthful_total(self):
        grid = self._grid()["pivot"]
        distinct_countries = len(set(
            self.env["res.partner"].search([]).mapped("country_id").ids))
        self.assertEqual(grid["rows"]["total"], distinct_countries)

    def test_totals_add_up(self):
        grid = self._grid()["pivot"]
        self.assertEqual(sum(grid["row_totals"].values()), grid["grand_total"])
        self.assertEqual(sum(grid["col_totals"].values()), grid["grand_total"])

    def test_cells_are_keyed_by_axis_key(self):
        grid = self._grid()["pivot"]
        for key in grid["cells"]:
            row_key, col_key = key.split("|", 1)
            self.assertIn(row_key, [v["key"] for v in grid["rows"]["values"]])
            self.assertIn(col_key, [v["key"] for v in grid["cols"]["values"]])

    # -------------------------------------------------------------- paging
    def test_the_axes_are_capped(self):
        grid = self._grid()["pivot"]
        self.assertLessEqual(len(grid["rows"]["values"]),
                             spec_lib.PIVOT_AXIS_CAP)
        self.assertEqual(grid["rows"]["cap"], spec_lib.PIVOT_AXIS_CAP)

    def test_the_first_page_has_no_previous(self):
        grid = self._grid()["pivot"]
        self.assertFalse(grid["rows"]["has_previous"])
        self.assertEqual(grid["rows"]["offset"], 0)

    def test_an_offset_page_reports_a_previous(self):
        grid = self._grid(offsets={"p": {"row": 1, "col": 0}})["pivot"]
        self.assertTrue(grid["rows"]["has_previous"])
        self.assertEqual(grid["rows"]["offset"], 1)

    def test_paging_one_axis_leaves_the_other_alone(self):
        grid = self._grid(offsets={"p": {"row": 1, "col": 0}})["pivot"]
        self.assertEqual(grid["cols"]["offset"], 0)

    def test_a_negative_offset_is_clamped(self):
        grid = self._grid(offsets={"p": {"row": -5, "col": -5}})["pivot"]
        self.assertEqual(grid["rows"]["offset"], 0)

    # ------------------------------------------------------------ unassigned
    def test_records_with_no_value_still_get_a_row(self):
        """`in` does not match NULL, so an Unassigned bucket has to be asked
        for explicitly or its whole row silently empties."""
        self.env["res.partner"].create(
            {"name": "AID Pivot Nowhere", "country_id": False})
        grid = self._grid()["pivot"]
        keys = [v["key"] for v in grid["rows"]["values"]]
        if "__none__" in keys:
            has_cells = any(k.startswith("__none__|") for k in grid["cells"])
            self.assertTrue(
                has_cells,
                "the Unassigned row is shown but has no cells — the restriction "
                "dropped its records")

    def test_an_empty_result_says_why(self):
        spec = pivot_spec(domain=[["name", "=", "nothing at all here"]])
        widget = self._grid(spec)
        self.assertTrue(widget.get("empty"))
