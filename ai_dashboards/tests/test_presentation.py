# -*- coding: utf-8 -*-
"""What the canvas is handed to draw: words, whole axes, honest slices.

The redesign leans on the server for three things the browser cannot work out
on its own — what a stored value is called, which buckets a date axis has,
and what "everything else" adds up to in a donut. These tests pin each one.
"""
import json

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.tools.misc import format_date

from ..models import ai_dashboard_spec as spec_lib
from .test_spec import minimal


def _widget(**values):
    widget = {"id": "w1", "type": "bar", "title": "T", "span": 6,
              "query": {"model": "res.partner", "group_by": ["country_id"],
                        "measures": ["__count"]}}
    widget.update(values)
    return widget


@tagged("post_install", "-at_install")
class TestPresentation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Render = cls.env["ai.dashboard.render"]
        countries = cls.env["res.country"].search([], limit=9, order="id")
        # Nine countries with a different number of partners each, so the
        # donut has a clear top six and a known remainder.
        cls.partners = cls.env["res.partner"].create([
            {"name": "AID Pres %s-%s" % (i, n), "country_id": country.id,
             "type": "contact" if n % 2 else "invoice"}
            for i, country in enumerate(countries)
            for n in range(i + 1)
        ])

    def _render(self, widgets, filters=None):
        spec = minimal(widgets=widgets)
        if filters:
            spec["filters"] = filters
        board = self.env["ai.dashboard"].create({
            "name": "Presentation", "state": "published",
            "spec_json": json.dumps(spec)})
        return self.Render.render(board.id)

    # ------------------------------------------------------------- labels
    def test_a_selection_is_shown_by_its_label_not_its_key(self):
        out = self._render([_widget(query={
            "model": "res.partner", "group_by": ["type"],
            "measures": ["__count"]})])
        labels = {p["raw"]: p["label"] for p in out["widgets"][0]["series"]}
        described = dict(self.env["res.partner"].fields_get(
            ["type"], ["selection"])["type"]["selection"])
        self.assertEqual(labels["invoice"], described["invoice"])
        self.assertNotEqual(labels["invoice"], "invoice")

    def test_a_month_reads_as_a_month(self):
        out = self._render([_widget(query={
            "model": "res.partner", "group_by": ["create_date:month"],
            "measures": ["__count"]})])
        first_of_month = fields.Date.context_today(self.env["res.partner"]) \
            .replace(day=1)
        point = out["widgets"][0]["series"][-1]
        self.assertEqual(point["label"], format_date(
            self.env, first_of_month, date_format="MMM yyyy"))
        self.assertEqual(point["short"], format_date(
            self.env, first_of_month, date_format="MMM"))
        # The drill-down still filters on the real date.
        self.assertEqual(point["raw"][:7],
                         fields.Date.to_string(first_of_month)[:7])

    def test_a_table_is_headed_in_words(self):
        out = self._render([_widget(type="table")])
        tile = out["widgets"][0]
        self.assertEqual(tile["column_keys"], ["country_id", "__count"])
        self.assertEqual(tile["columns"][1], "Count")
        self.assertEqual(tile["columns"][0], self.env["res.partner"].fields_get(
            ["country_id"], ["string"])["country_id"]["string"])
        self.assertEqual(tile["numeric_from"], 1)

    def test_the_currency_comes_with_its_id(self):
        out = self._render([_widget()])
        self.assertEqual(out["currency"]["id"], self.env.company.currency_id.id)

    # ------------------------------------------------------------ date axes
    def test_a_date_axis_has_every_bucket_up_to_today(self):
        out = self._render(
            [_widget(query={"model": "res.partner",
                            "group_by": ["create_date:month"],
                            "measures": ["__count"]})],
            filters=[{"key": "period", "type": "date_range", "label": "Period",
                      "default": "this_year", "field": "create_date"}])
        series = out["widgets"][0]["series"]
        today = fields.Date.context_today(self.env["res.partner"])
        # January to this month, each once, in order — the empty ones at zero.
        self.assertEqual(len(series), today.month)
        self.assertEqual([p["raw"][:7] for p in series],
                         ["%04d-%02d" % (today.year, m)
                          for m in range(1, today.month + 1)])
        self.assertGreaterEqual(series[-1]["value"], len(self.partners))

    def test_a_top_ten_is_not_filled_like_an_axis(self):
        out = self._render([_widget(query={
            "model": "res.partner", "group_by": ["create_date:day"],
            "measures": ["__count"], "limit": 3})])
        self.assertLessEqual(len(out["widgets"][0]["series"]), 3)

    # --------------------------------------------------------------- rings
    def test_a_donut_keeps_six_slices_and_an_honest_other(self):
        out = self._render([_widget(type="donut")])
        series = out["widgets"][0]["series"]
        self.assertEqual(len(series), 7)
        other = series[-1]
        self.assertTrue(other.get("other"))
        self.assertIsNone(other["raw"])
        total = self.env["res.partner"].search_count([])
        # Everything is accounted for: six slices plus Other is the whole.
        self.assertEqual(sum(p["value"] for p in series), total)
        values = [p["value"] for p in series[:-1]]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_an_average_is_never_folded_into_other(self):
        out = self._render([_widget(type="donut", query={
            "model": "res.partner", "group_by": ["country_id"],
            "measures": ["id:avg"]})])
        self.assertFalse(any(p.get("other")
                             for p in out["widgets"][0]["series"]))

    # ---------------------------------------------------------------- KPIs
    def _kpi(self, **values):
        return _widget(type="kpi", query={"model": "res.partner", "group_by": [],
                                          "measures": ["__count"]}, **values)

    def test_a_kpi_with_a_window_carries_its_trend(self):
        out = self._render(
            [self._kpi()],
            filters=[{"key": "period", "type": "date_range", "label": "Period",
                      "default": "last_30_days", "field": "create_date"}])
        trend = out["widgets"][0]["trend"]
        self.assertEqual(len(trend), 31)
        self.assertGreaterEqual(trend[-1], len(self.partners))

    def test_a_kpi_without_a_window_has_no_trend(self):
        out = self._render([self._kpi()])
        self.assertNotIn("trend", out["widgets"][0])

    def test_negating_a_kpi_negates_its_trend(self):
        out = self._render(
            [self._kpi(format={"kind": "integer", "negate": True})],
            filters=[{"key": "period", "type": "date_range", "label": "Period",
                      "default": "last_30_days", "field": "create_date"}])
        self.assertLessEqual(out["widgets"][0]["trend"][-1],
                             -len(self.partners))

    # --------------------------------------------------------------- ranges
    def test_a_window_is_named_for_what_it_is(self):
        from datetime import date

        from ..models import ai_dashboard_labels as labels_lib
        words = lambda *a, **k: labels_lib.range_label(self.env, *a, **k)
        with self.subTest("a whole month, compact"):
            self.assertEqual(words(date(2026, 10, 1), date(2026, 11, 1)),
                             format_date(self.env, date(2026, 10, 1),
                                         date_format="MMM yyyy"))
        with self.subTest("a whole year, compact"):
            self.assertEqual(words(date(2026, 1, 1), date(2027, 1, 1)), "2026")
        with self.subTest("the same month, spelled out"):
            self.assertTrue(words(date(2026, 11, 1), date(2026, 12, 1),
                                  compact=False).startswith("1 – 30 "))
        with self.subTest("across two months"):
            self.assertIn(" – ", words(date(2026, 10, 28), date(2026, 11, 4)))
        with self.subTest("nothing"):
            self.assertEqual(words(date(2026, 1, 2), date(2026, 1, 2)), "")

    def test_a_period_filter_says_which_dates_it_covers(self):
        out = self._render(
            [_widget()],
            filters=[{"key": "period", "type": "date_range", "label": "Period",
                      "default": "this_month", "field": "create_date"}])
        period = out["filters"][0]
        self.assertTrue(period["range"].startswith("1 – "))
        self.assertTrue(out["widgets"][0]["window_label"])

    # ------------------------------------------------------------- polarity
    def test_polarity_is_validated_and_passed_through(self):
        out = self._render([self._kpi(polarity="down_is_good")])
        self.assertEqual(out["widgets"][0]["polarity"], "down_is_good")
        out = self._render([self._kpi()])
        self.assertEqual(out["widgets"][0]["polarity"], "up_is_good")
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(minimal(widgets=[self._kpi(polarity="sideways")]))

    def test_the_tile_count_follows_the_spec(self):
        board = self.env["ai.dashboard"].create({
            "name": "Count", "spec_json": json.dumps(minimal(widgets=[
                _widget(), _widget(id="w2")]))})
        self.assertEqual(board.widget_count, 2)
