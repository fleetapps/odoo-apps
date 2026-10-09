"""Explain: the drivers of a change always add up to it, for a line, an
account and a subtotal; the narrative can only cite them."""

from odoo.tests import tagged

from .common import AiCase, message


@tagged("post_install", "-at_install")
class TestExplain(AiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.alpha = cls.env["res.partner"].create({"name": "Alpha"})
        cls.beta = cls.env["res.partner"].create({"name": "Beta"})
        cls.software = cls.env["account.account"].create(
            {"name": "Software", "code": "630099", "account_type": "expense"})
        cls.post("2026-08-10", [
            (cls.revenue, -800.0, {"partner": cls.alpha}), (cls.revenue, -200.0, {"partner": cls.beta}),
            (cls.cos, 300.0), (cls.opex, 100.0), (cls.software, 50.0)])
        cls.post("2026-09-10", [
            (cls.revenue, -1100.0, {"partner": cls.alpha}), (cls.revenue, -150.0, {"partner": cls.beta}),
            (cls.cos, 450.0), (cls.opex, 90.0), (cls.software, 210.0)])
        cls.Explain = cls.env["odin.ai.explain"].with_user(cls.accountant)

    def explain(self, key):
        return self.Explain.explain(self.options(), key, "c0")

    def test_a_line_splits_into_its_accounts(self):
        result = self.explain("L:OPEX")
        self.assertAlmostEqual(result["delta"], 150.0)
        self.assertAlmostEqual(sum(d["delta"] for d in result["drivers"]), result["delta"])
        self.assertEqual(result["drivers"][0]["name"].split(" ", 1)[-1], "Software")
        self.assertEqual(result["previous_label"], "Aug 2026")

    def test_an_account_splits_into_its_partners(self):
        result = self.explain(f"L:REV/A:{self.revenue.id}")
        self.assertEqual(result["driver_kind"], "partner")
        deltas = {d["name"]: d["delta"] for d in result["drivers"]}
        self.assertAlmostEqual(deltas["Alpha"], 300.0)
        self.assertAlmostEqual(deltas["Beta"], -50.0)
        self.assertAlmostEqual(sum(deltas.values()), result["delta"])
        self.assertTrue(result["items"], "the largest items of the period are listed")

    def test_a_subtotal_splits_into_the_lines_it_is_built_from(self):
        result = self.explain("L:GP")
        self.assertAlmostEqual(result["delta"], 250.0 - 150.0)
        effects = {d["code"]: d["delta"] for d in result["drivers"]}
        self.assertAlmostEqual(effects["REV"], 250.0)
        self.assertAlmostEqual(effects["COS"], -150.0, msg="a cost going up lowers gross profit")
        self.assertAlmostEqual(sum(effects.values()), result["delta"])
        # Through formulas of formulas: Net Profit = Operating Profit + ... = Gross Profit - ...
        result = self.explain("L:NET")
        self.assertAlmostEqual(sum(d["delta"] for d in result["drivers"]), result["delta"])
        self.assertIn("OPEX", {d["code"] for d in result["drivers"]})

    def test_the_narrative_cites_only_known_figures(self):
        explanation = self.explain("L:OPEX")
        requests = self.mock_api(message({
            "headline": "Operating expenses rose by 150.",
            "points": [{"text": "Software [E1].", "evidence": ["E1", "E42"]}],
            "caveats": [],
        }))
        story = self.Explain.narrate(explanation)
        self.assertEqual(story["points"][0]["evidence"], ["E1"])
        self.assertEqual(requests[0]["output_config"]["effort"], "low")
        self.assertIn('"ref": "E1"', requests[0]["messages"][0]["content"])

    def test_works_without_the_ai(self):
        self.env.company.odin_ai_consent = False
        result = self.explain("L:OPEX")
        self.assertTrue(result["ai"], "the page says why there is no summary")
        self.assertTrue(result["drivers"])
