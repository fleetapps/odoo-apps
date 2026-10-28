from odoo import Command
from odoo.tests import tagged

from .test_motor import MotorCase


@tagged("post_install", "-at_install")
class TestMotorProducts(MotorCase):
    """Motor rates belong to products: a band added on a product is quoted
    at once, an insurer with two products is quoted on both, a quoted band
    is archived rather than deleted."""

    def test_rates_get_products(self):
        self.assertTrue(self.definite_private.product_id)
        self.assertEqual(self.fidelity_low.product_id.line, "motor")
        self.assertNotEqual(self.fidelity_low.product_id, self.fidelity_high.product_id,
                            "different vehicle ages: different products")
        product = self.definite_private.product_id
        self.assertEqual(product.name, "Test Definite – %s – Comprehensive" % self.private.name)
        self.assertEqual(product.insurer_id, self.definite)

    def test_band_on_product_is_quoted(self):
        pickup = self.env.ref("odin_motor_quote.class_pickup")
        product = self.env["odin.cover.product"].create({
            "line": "motor", "insurer_id": self.definite.id, "class_id": pickup.id,
            "cover": "tpo", "period_months": 12,
            "rate_ids": [Command.create({"tonnage_min": 0, "tonnage_max": 3, "flat_premium": 5000})],
        })
        self.assertEqual(product.name, "Test Definite – %s – Third party only" % pickup.name)
        setup = self.Motor.get_setup()
        covers = next(c for c in setup["classes"] if c["id"] == pickup.id)["covers"]
        self.assertIn("tpo", covers, "the third party button appears by itself")
        plans = self.Motor.search_plans({"cover": {"class_id": pickup.id, "cover": "tpo",
                                                   "tonnage": 2}})["plans"]
        self.assertEqual([p["insurer"] for p in plans], ["Test Definite"])
        self.assertEqual(plans[0]["core"]["amount"], 5000)
        self.assertIn("5,000", product.try_html or "")

    def test_shared_values_follow_product(self):
        product = self.fidelity_low.product_id
        product.pvt = "included"
        self.assertEqual(self.fidelity_low.pvt, "included")
        product.action_archive()
        self.assertFalse(self.fidelity_low.active)
        product.action_unarchive()
        self.assertTrue(self.fidelity_low.active)

    def test_two_products_one_insurer(self):
        premier = self.env["odin.cover.product"].create({
            "line": "motor", "insurer_id": self.definite.id, "class_id": self.private.id,
            "cover": "comprehensive", "quote_label": "Premier", "max_age": 20,
            "rate_ids": [Command.create({"value_min": 500000, "rate": 5, "min_premium": 30000})],
        })
        plans = self.plans()
        definite = [p for p in plans.values() if p["insurer"] == "Test Definite"]
        self.assertEqual(len(definite), 2, "both products quoted")
        self.assertIn("Premier", plans[premier.rate_ids.id]["name"])
        self.assertIn("already has a product", premier.warning_html)

    def test_quoted_band_archived(self):
        result = self.Motor.save_quote({
            "customer": {"source": "new", "name": "Driver", "phone": "0711000000"},
            "cover": self.car(),
            "plans": [{"rate_id": self.definite_private.id, "options": []}],
        })
        self.assertTrue(result["comparison"]["id"])
        product = self.definite_private.product_id
        product.rate_ids = [Command.delete(self.definite_private.id)]
        self.assertTrue(self.definite_private.exists())
        self.assertFalse(self.definite_private.active)
        self.assertTrue(self.definite_private.odin_removed)
        product.action_archive()
        product.action_unarchive()
        self.assertFalse(self.definite_private.active, "a removed band stays removed")

    def test_band_warnings(self):
        product = self.fidelity_low.product_id
        product.rate_ids = [Command.create({"value_min": 1200000, "value_max": 1999999, "rate": 450,
                                            "min_premium": 0})]
        warnings = product.warning_html
        self.assertIn("No rate between", warnings)
        self.assertIn("looks high", warnings)
        self.assertIn("no minimum premium", warnings)

    def test_renewal_only_for_annual_cover(self):
        monthly = self.env["odin.motor.rate"].create({
            "insurer_id": self.definite.id, "class_id": self.psv.id, "cover": "tpo",
            "period_months": 1, "seats_min": 10, "seats_max": 17, "flat_premium": 1500,
        })
        saved = self.Motor.save_quote({
            "customer": {"source": "new", "name": "Matatu Owner", "phone": "0722000000"},
            "cover": {"class_id": self.psv.id, "cover": "tpo", "seats": 14, "period": 1},
            "plans": [{"rate_id": monthly.id, "options": []}],
        })
        comparison = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        comparison.plan_ids.order_id.action_confirm()
        self.assertFalse(comparison.odin_next_renewal_id, "a one-month cover is not renewed")
        saved = self.Motor.save_quote({
            "customer": {"source": "new", "name": "Car Owner", "phone": "0733000000"},
            "cover": self.car(),
            "plans": [{"rate_id": self.definite_private.id, "options": []}],
        })
        comparison = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        comparison.plan_ids.order_id.action_confirm()
        renewal = comparison.odin_next_renewal_id
        self.assertEqual(renewal.line, "motor")
        self.assertEqual(renewal.insurer_id, self.definite)
        self.assertEqual(renewal.product_id, self.definite_private.product_id)
        self.assertTrue(renewal.start_to_confirm, "confirmed without a start date")
