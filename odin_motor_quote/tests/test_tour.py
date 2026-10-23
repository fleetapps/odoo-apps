from odoo.tests import HttpCase, tagged

from .test_motor import MotorCase


@tagged("post_install", "-at_install")
class TestMotorTour(MotorCase, HttpCase):
    """From the Quotes list: New ▸ Motor quote, a car, an insurer, saved."""

    def test_tour(self):
        self.agent.password = "quote.agent"
        self.start_tour("/odoo/action-odin_client_quote.action_quote_comparison",
                        "odin_motor_quote_tour", login="quote.agent")
        comparison = self.env["odin.quote.comparison"].search(
            [("partner_id.name", "=", "Tour Motorist")])
        self.assertEqual(comparison.odin_quote_type, "motor")
        self.assertEqual(comparison.plan_ids.order_id.odin_motor_rate_id, self.definite_private)
