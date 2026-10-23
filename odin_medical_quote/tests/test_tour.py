from odoo.tests import HttpCase, tagged

from .common import MedicalQuoteCase


@tagged("post_install", "-at_install")
class TestMedicalQuoteTour(MedicalQuoteCase, HttpCase):
    """Add Medical Quote in a real browser, as a salesperson."""

    def test_tour(self):
        # Afya prices by family size, so the figures hold whatever the year.
        self.jamii.odin_medical = False
        self.agent.password = "quote.agent"
        self.start_tour(
            "/odoo/action-odin_medical_quote.action_medical_quote",
            "odin_medical_quote_tour", login="quote.agent")
        comparison = self.env["odin.quote.comparison"].search(
            [("partner_id.name", "=", "Tour Prospect")])
        self.assertEqual(len(comparison), 1)
        order = comparison.plan_ids.order_id
        self.assertEqual(order.sale_order_template_id, self.afya)
        self.assertAlmostEqual(order.amount_total, 44240.01, delta=0.015)
        self.assertEqual(comparison.opportunity_id.partner_id, comparison.partner_id)
        self.assertEqual(comparison.partner_id.phone, "+254711222333")
