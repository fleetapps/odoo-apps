# -*- coding: utf-8 -*-
"""The specification validator.

This is the security boundary of the module, so it is tested like one rather
than like a parser. Every case below is something an assistant could plausibly
produce — by accident or because something upstream of it was hostile — and the
correct behaviour is always a refusal that names the problem, never a quiet
acceptance and never a traceback.

The rule these all defend: a spec is *data*. Nothing in it may express "run
this", only "read that".
"""
from odoo.tests import TransactionCase, tagged

from ..models import ai_dashboard_spec as spec_lib


def minimal(**overrides):
    spec = {
        "schema": spec_lib.SCHEMA_ID,
        "title": "Partners",
        "widgets": [{
            "id": "w1",
            "type": "bar",
            "title": "By country",
            "span": 6,
            "query": {
                "model": "res.partner",
                "group_by": ["country_id"],
                "measures": ["__count"],
            },
        }],
    }
    spec.update(overrides)
    return spec


@tagged("post_install", "-at_install")
class TestSpecStructure(TransactionCase):
    """Structural rules, checked without touching the database."""

    def test_a_minimal_spec_is_accepted(self):
        out = spec_lib.validate(minimal())
        self.assertEqual(out["schema"], spec_lib.SCHEMA_ID)
        self.assertEqual(len(out["widgets"]), 1)

    def test_unknown_keys_are_refused_not_ignored(self):
        """Dropping a key silently produces a dashboard that is subtly not the
        one anybody asked for, and nothing downstream can tell."""
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(minimal(onclick="alert(1)"))

    def test_underscore_keys_are_refused(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(minimal(_eval="x"))

    def test_the_schema_id_must_match(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(minimal(schema="ai-dashboards/99"))

    def test_a_dashboard_needs_a_widget(self):
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(minimal(widgets=[]))

    def test_widget_ids_must_be_unique(self):
        spec = minimal()
        spec["widgets"] = spec["widgets"] * 2
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    # ------------------------------------------------------------ the domain
    def test_a_domain_may_not_be_an_expression(self):
        """literal_eval, never eval: a domain that calls something is refused
        outright rather than evaluated in any sandbox."""
        spec = minimal()
        spec["widgets"][0]["query"]["domain"] = \
            "__import__('os').system('id')"
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_unknown_domain_operators_are_refused(self):
        spec = minimal()
        spec["widgets"][0]["query"]["domain"] = [["name", "EXEC", 1]]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_a_valid_domain_survives_as_a_list(self):
        spec = minimal()
        spec["widgets"][0]["query"]["domain"] = [["is_company", "=", True]]
        out = spec_lib.validate(spec)
        self.assertEqual(out["widgets"][0]["query"]["domain"],
                         [["is_company", "=", True]])

    def test_a_domain_string_is_parsed(self):
        spec = minimal()
        spec["widgets"][0]["query"]["domain"] = "[('is_company', '=', True)]"
        out = spec_lib.validate(spec)
        self.assertEqual(out["widgets"][0]["query"]["domain"],
                         [["is_company", "=", True]])

    def test_an_over_long_domain_is_refused(self):
        spec = minimal()
        spec["widgets"][0]["query"]["domain"] = [
            ["name", "!=", str(i)] for i in range(spec_lib.MAX_DOMAIN_TERMS + 1)]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    # ------------------------------------------------------------- the query
    def test_context_can_never_be_smuggled_in(self):
        """`context` is how ORM flags like active_test get changed. There is no
        legitimate reason for a spec to carry one."""
        spec = minimal()
        spec["widgets"][0]["query"]["context"] = {"active_test": False}
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_measures_must_name_a_real_aggregate(self):
        spec = minimal()
        spec["widgets"][0]["query"]["measures"] = ["credit:exec"]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_a_bare_field_is_not_a_measure(self):
        spec = minimal()
        spec["widgets"][0]["query"]["measures"] = ["credit"]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_group_by_granularity_must_be_known(self):
        spec = minimal()
        spec["widgets"][0]["query"]["group_by"] = ["create_date:fortnight"]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_order_must_be_a_field_and_a_direction(self):
        spec = minimal()
        spec["widgets"][0]["query"]["order"] = "name; DROP TABLE"
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    # ------------------------------------------------------- widget grammar
    def test_a_kpi_may_not_group(self):
        spec = minimal()
        spec["widgets"][0].update(type="kpi")
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_a_bar_chart_needs_exactly_one_grouping(self):
        spec = minimal()
        spec["widgets"][0]["query"]["group_by"] = ["country_id", "is_company"]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_span_must_fit_the_grid(self):
        spec = minimal()
        spec["widgets"][0]["span"] = 99
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_unsupported_widget_types_are_refused(self):
        spec = minimal()
        spec["widgets"][0]["type"] = "iframe"
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec)

    def test_a_refusal_names_the_alternatives(self):
        """A model told only that a key is wrong tries a synonym; one shown the
        whole set corrects on the first retry."""
        spec = minimal()
        spec["widgets"][0]["type"] = "iframe"
        with self.assertRaises(spec_lib.SpecError) as caught:
            spec_lib.validate(spec)
        message = str(caught.exception)
        self.assertIn("bar", message)
        self.assertIn("kpi", message)


@tagged("post_install", "-at_install")
class TestSpecAgainstDatabase(TransactionCase):
    """The checks that need a real model, and the governance scope."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scope = cls.env["mcp.scope"].create({
            "name": "TEST dashboards",
            "read_only": True,
            "line_ids": [(0, 0, {
                "model_id": cls.env["ir.model"]._get("res.partner").id,
                "can_read": True,
                "field_blacklist": "credit_limit",
            })],
        })

    def test_a_field_that_does_not_exist_is_refused(self):
        spec = minimal()
        spec["widgets"][0]["query"]["group_by"] = ["definitely_not_a_field"]
        with self.assertRaises(spec_lib.SpecError) as caught:
            spec_lib.validate(spec, self.env, self.scope)
        self.assertIn("get_schema", str(caught.exception),
                      "the refusal should tell the model how to find out")

    def test_a_model_outside_the_scope_is_refused(self):
        """Authorship is gated by the governance scope: an assistant may only
        build over data the connection could already read."""
        spec = minimal()
        spec["widgets"][0]["query"]["model"] = "res.users"
        spec["widgets"][0]["query"]["group_by"] = ["login"]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec, self.env, self.scope)

    def test_a_blacklisted_field_is_refused(self):
        spec = minimal()
        spec["widgets"][0]["query"]["measures"] = ["credit_limit:sum"]
        spec["widgets"][0]["query"]["group_by"] = ["country_id"]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec, self.env, self.scope)

    def test_a_non_numeric_field_cannot_be_totalled(self):
        spec = minimal()
        spec["widgets"][0]["query"]["measures"] = ["name:sum"]
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec, self.env, self.scope)

    def test_an_uninstalled_model_is_refused(self):
        spec = minimal()
        spec["widgets"][0]["query"]["model"] = "not.a.model"
        with self.assertRaises(spec_lib.SpecError):
            spec_lib.validate(spec, self.env, self.scope)

    def test_a_good_spec_passes_every_gate(self):
        out = spec_lib.validate(minimal(), self.env, self.scope)
        self.assertEqual(out["widgets"][0]["query"]["model"], "res.partner")

    # -------------------------------------------------------------- describe
    def test_describe_says_what_it_reads(self):
        """The panel an administrator uses to decide whether to trust it.
        Nobody generating SQL can offer this."""
        prose = spec_lib.describe(
            spec_lib.validate(minimal(), self.env, self.scope), self.env)
        self.assertIn("res.partner", prose)
        self.assertIn("own Odoo permissions", prose)

    def test_describe_survives_an_empty_spec(self):
        self.assertTrue(spec_lib.describe({}, self.env))
