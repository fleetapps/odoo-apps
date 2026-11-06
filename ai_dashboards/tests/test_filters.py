# -*- coding: utf-8 -*-
"""Filters a reader can change: a period, a record picker, a status list.

The promise is simple to state and easy to break quietly: a filter narrows
every tile it can, passes the others by, and never turns a working tile into
a broken one. A vendor filter that silently missed half the page, or one that
crashed the bank card because journals have no vendor, would each look like a
dashboard that works.
"""
import json

from odoo.tests import TransactionCase, tagged

from ..models import ai_dashboard_spec as spec_lib
from .test_spec import minimal


def with_filters(*filters, widgets=None):
    spec = minimal()
    spec["filters"] = list(filters)
    if widgets is not None:
        spec["widgets"] = widgets
    return spec


COUNTRY = {"key": "country", "type": "many2one", "label": "Country",
           "field": "country_id"}
ADDRESS = {"key": "kind", "type": "selection", "label": "Address type",
           "field": "type"}
PARTNER_KPI = {"id": "people", "type": "kpi", "title": "Contacts", "span": 3,
               "query": {"model": "res.partner", "measures": ["__count"]}}
COUNTRY_KPI = {"id": "countries", "type": "kpi", "title": "Countries",
               "span": 3, "query": {"model": "res.country",
                                    "measures": ["__count"]}}


@tagged("post_install", "-at_install")
class TestFilterSpec(TransactionCase):
    """The shape of a filter, checked without the database."""

    def test_a_record_filter_needs_a_field(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(
                {"key": "c", "type": "many2one", "label": "C"}))

    def test_a_record_filter_sits_on_the_tiles_own_model(self):
        """A path through another record has no single picker to offer."""
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(
                dict(COUNTRY, field="parent_id.country_id")))

    def test_a_default_becomes_a_list_of_ids(self):
        out = spec_lib.validate(with_filters(dict(COUNTRY, default=7)))
        self.assertEqual(out["filters"][0]["default"], [7])

    def test_a_default_that_is_not_an_id_is_refused(self):
        for bad in ("Kenya", [1, "x"], [0], -3, True):
            with self.assertRaises(spec_lib.SpecError, msg=repr(bad)):
                spec_lib.validate(with_filters(dict(COUNTRY, default=bad)))

    def test_only_a_record_filter_takes_a_domain(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(
                dict(ADDRESS, domain=[["active", "=", True]])))

    def test_a_record_filters_domain_is_parsed_like_any_other(self):
        out = spec_lib.validate(with_filters(
            dict(COUNTRY, domain="[('code', '!=', False)]")))
        self.assertEqual(out["filters"][0]["domain"], [["code", "!=", False]])
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(
                dict(COUNTRY, domain=[["code", "exec", "x"]])))

    def test_underscore_keys_are_reserved_for_the_canvas(self):
        """`__compare` and `__period:<id>` live beside the filters."""
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(dict(COUNTRY, key="__compare")))

    def test_a_selection_default_is_text(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(dict(ADDRESS, default=3)))


@tagged("post_install", "-at_install")
class TestFilterValues(TransactionCase):
    """What a filter narrows to — pure, so every caller agrees."""

    def test_the_readers_choice_beats_the_default(self):
        flt = dict(COUNTRY, default=[1])
        self.assertEqual(spec_lib.filter_value(flt, {"country": [2, 3]}), [2, 3])
        self.assertEqual(spec_lib.filter_value(flt, {}), [1])

    def test_choosing_all_is_a_choice(self):
        """Present-but-empty is "everything", not "back to the default"."""
        flt = dict(COUNTRY, default=[1])
        self.assertIsNone(spec_lib.filter_value(flt, {"country": []}))
        sel = dict(ADDRESS, default="invoice")
        self.assertIsNone(spec_lib.filter_value(sel, {"kind": ""}))

    def test_anything_that_is_not_an_id_is_dropped(self):
        """A string reaching the ORM would be matched against names."""
        self.assertEqual(spec_lib.record_ids(["4", 4, "x", True, -1, 0, 9]),
                         [4, 9])
        self.assertIsNone(spec_lib.filter_value(COUNTRY, {"country": "Kenya"}))

    def test_a_period_nobody_offers_falls_back_to_the_default(self):
        flt = {"key": "p", "type": "date_range", "default": "this_month"}
        self.assertEqual(spec_lib.filter_value(flt, {"p": "forever"}),
                         "this_month")
        self.assertIsNone(spec_lib.filter_value(flt, {"p": "all_time"}))

    def test_a_card_period_replaces_the_page_period_and_keeps_the_rest(self):
        spec = with_filters(
            {"key": "period", "type": "date_range", "default": "this_year"},
            COUNTRY)
        card = {"id": "c", "type": "line", "title": "T", "period": "last_month"}
        reached = spec_lib.filters_for_widget(card, spec)
        self.assertEqual([f["key"] for f in reached],
                         ["country", spec_lib.card_period_key("c")])

    def test_a_position_card_takes_no_period(self):
        """An aging card counting only this year's invoices hides old debt."""
        spec = with_filters(
            {"key": "period", "type": "date_range", "default": "this_year"},
            COUNTRY)
        for kind in spec_lib.POSITION_CARDS:
            reached = spec_lib.filters_for_widget({"id": "a", "type": kind}, spec)
            self.assertEqual([f["key"] for f in reached], ["country"])


@tagged("post_install", "-at_install")
class TestFilterAgainstDatabase(TransactionCase):
    """A filter has to land on something real, as the right kind of field."""

    def test_a_filter_no_tile_can_answer_is_refused(self):
        """A control that changes nothing is worse than no control."""
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(
                dict(COUNTRY, field="x_no_such_field")), self.env)

    def test_the_field_must_be_the_kind_the_filter_says(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(dict(COUNTRY, field="type")),
                              self.env)

    def test_a_selection_default_must_be_one_of_its_values(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(dict(ADDRESS, default="nope")),
                              self.env)
        spec_lib.validate(with_filters(dict(ADDRESS, default="invoice")),
                          self.env)

    def test_a_pickers_domain_is_checked_on_the_records_it_offers(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(with_filters(
                dict(COUNTRY, domain=[["not_a_field", "=", 1]])), self.env)
        spec_lib.validate(with_filters(
            dict(COUNTRY, domain=[["code", "!=", False]])), self.env)

    def test_tiles_without_the_field_are_fine(self):
        """Only *no* tile having it is a mistake."""
        spec_lib.validate(with_filters(
            COUNTRY, widgets=[PARTNER_KPI, COUNTRY_KPI]), self.env)

    def test_the_schema_example_validates_against_a_real_database(self):
        if "sale.order" not in self.env:
            self.skipTest("Sales is not installed")
        example = self.env["mcp.engine"]._schema_example()
        spec_lib.validate(example, self.env)


@tagged("post_install", "-at_install")
class TestFilterRender(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Render = cls.env["ai.dashboard.render"]
        cls.kenya = cls.env.ref("base.ke")
        cls.uganda = cls.env.ref("base.ug")
        cls.env["res.partner"].create([
            {"name": "AID Filter KE 1", "country_id": cls.kenya.id},
            {"name": "AID Filter KE 2", "country_id": cls.kenya.id,
             "type": "invoice"},
            {"name": "AID Filter UG", "country_id": cls.uganda.id},
        ])

    def _board(self, spec):
        return self.env["ai.dashboard"].create({
            "name": "Filter board", "spec_json": json.dumps(spec),
            "state": "published"})

    def _count(self, domain):
        return self.env["res.partner"].search_count(domain)

    def _kpi(self, board, values=None, widget="people"):
        out = self.Render.render(board.id, values or {})
        return next(w for w in out["widgets"] if w["id"] == widget), out

    # ------------------------------------------------------ record picker
    def test_a_record_filter_narrows_the_tile(self):
        board = self._board(with_filters(COUNTRY, widgets=[PARTNER_KPI]))
        tile, _out = self._kpi(board, {"country": [self.kenya.id]})
        self.assertEqual(tile["value"],
                         self._count([("country_id", "in", [self.kenya.id])]))
        self.assertIn(["country_id", "in", [self.kenya.id]], tile["domain"])

    def test_several_records_mean_any_of_them(self):
        board = self._board(with_filters(COUNTRY, widgets=[PARTNER_KPI]))
        both = [self.kenya.id, self.uganda.id]
        tile, _out = self._kpi(board, {"country": both})
        self.assertEqual(tile["value"],
                         self._count([("country_id", "in", both)]))

    def test_the_default_applies_until_the_reader_clears_it(self):
        board = self._board(with_filters(
            dict(COUNTRY, default=[self.uganda.id]), widgets=[PARTNER_KPI]))
        untouched, _out = self._kpi(board)
        self.assertIn(["country_id", "in", [self.uganda.id]],
                      untouched["domain"])
        cleared, _out = self._kpi(board, {"country": []})
        self.assertFalse(any(t[0] == "country_id" for t in cleared["domain"]))

    def test_ids_sent_as_text_are_numbers_by_the_time_they_reach_the_orm(self):
        board = self._board(with_filters(COUNTRY, widgets=[PARTNER_KPI]))
        tile, _out = self._kpi(board, {"country": [str(self.kenya.id), "x"]})
        self.assertIn(["country_id", "in", [self.kenya.id]], tile["domain"])

    def test_a_tile_without_the_field_is_passed_by_not_broken(self):
        """The bank-journal case: no vendor on a journal is not an error."""
        board = self._board(with_filters(
            COUNTRY, widgets=[PARTNER_KPI, COUNTRY_KPI]))
        tile, _out = self._kpi(board, {"country": [self.kenya.id]},
                               widget="countries")
        self.assertNotIn("error", tile)
        self.assertFalse(any(t[0] == "country_id" for t in tile["domain"]))
        self.assertEqual(tile["value"], self.env["res.country"].search_count([]))

    def test_a_same_named_field_pointing_elsewhere_is_passed_by(self):
        """parent_id is a company on a partner and a category on a tag."""
        flt = {"key": "parent", "type": "many2one", "field": "parent_id",
               "relation": "res.partner"}
        self.assertTrue(self.Render._filter_meta("res.partner", flt))
        self.assertIsNone(self.Render._filter_meta("res.partner.category", flt))

    def test_the_payload_tells_the_bar_what_to_draw(self):
        board = self._board(with_filters(
            COUNTRY, ADDRESS, widgets=[PARTNER_KPI]))
        _tile, out = self._kpi(board, {"country": [self.kenya.id]})
        country, kind = out["filters"]
        self.assertEqual(country["relation"], "res.country")
        self.assertEqual(country["value"], [self.kenya.id])
        self.assertTrue(country["available"])
        self.assertIn("invoice", [value for value, _label in kind["options"]])
        self.assertEqual(kind["value"], "")

    # ---------------------------------------------------------- selection
    def test_a_selection_filter_narrows_the_tile(self):
        board = self._board(with_filters(ADDRESS, widgets=[PARTNER_KPI]))
        tile, _out = self._kpi(board, {"kind": "invoice"})
        self.assertEqual(tile["value"], self._count([("type", "=", "invoice")]))

    def test_a_value_the_field_does_not_have_is_ignored(self):
        """Matching nothing would read as "there are none"."""
        board = self._board(with_filters(ADDRESS, widgets=[PARTNER_KPI]))
        tile, out = self._kpi(board, {"kind": "made-up"})
        self.assertFalse(any(t[0] == "type" for t in tile["domain"]))
        self.assertEqual(out["filters"][0]["value"], "")

    # ------------------------------------------------------------- periods
    def test_a_period_lands_on_the_date_field_each_model_has(self):
        """A filter naming a field one model lacks must not break that tile."""
        self.assertEqual(
            self.Render._date_field_for("res.partner", "write_date"),
            "write_date")
        self.assertEqual(
            self.Render._date_field_for("res.country", "date_order"),
            self.Render._date_field("res.country"))
        self.assertEqual(
            self.Render._date_field_for("res.partner", "name"),
            self.Render._date_field("res.partner"))

    def test_a_card_with_its_own_period_still_takes_the_record_filter(self):
        """Replacing the filter list used to drop everything but the period."""
        card = {"id": "card", "type": "line", "title": "New contacts",
                "span": 4, "period": "this_year", "caption": "Added",
                "query": {"model": "res.partner",
                          "group_by": ["create_date:month"],
                          "measures": ["__count"]}}
        spec = spec_lib.validate(with_filters(
            {"key": "period", "type": "date_range", "default": "last_year",
             "field": "create_date"},
            COUNTRY, widgets=[card]), self.env)
        drawn = self.Render._render_widget(
            spec["widgets"][0], spec, {"country": [self.kenya.id]}, "none")
        self.assertIn(["country_id", "in", [self.kenya.id]], drawn["domain"])
        windows = [t for t in drawn["domain"] if t[0] == "create_date"]
        self.assertEqual(len(windows), 2, "the card's period, not two periods")
        start, _end = self.Render._period_bounds("this_year")
        self.assertIn(["create_date", ">=", str(start)], drawn["domain"])

    # ------------------------------------------------------------ drilling
    def test_the_records_behind_a_tile_are_the_filtered_ones(self):
        bar = dict(minimal()["widgets"][0])
        board = self._board(with_filters(COUNTRY, widgets=[bar]))
        action = self.Render.drill(board.id, "w1", None,
                                   {"country": [self.uganda.id]})
        self.assertIn(["country_id", "in", [self.uganda.id]], action["domain"])

    def test_a_cards_drill_opens_its_own_period(self):
        card = {"id": "card", "type": "line", "title": "New contacts",
                "span": 4, "period": "this_year",
                "query": {"model": "res.partner",
                          "group_by": ["create_date:month"],
                          "measures": ["__count"]}}
        board = self._board(with_filters(widgets=[card]))
        action = self.Render.drill(
            board.id, "card", None,
            {spec_lib.card_period_key("card"): "last_year"})
        start, _end = self.Render._period_bounds("last_year")
        self.assertIn(["create_date", ">=", str(start)], action["domain"])
