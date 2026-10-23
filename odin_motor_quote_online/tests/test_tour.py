from odoo.tests import HttpCase, tagged

from .test_online_motor import OnlineMotorCase


@tagged("post_install", "-at_install")
class TestOnlineMotorTour(OnlineMotorCase, HttpCase):
    """A stranger prices their car in a real browser, without logging in."""

    def test_tour(self):
        self.start_tour("/motor-quote", "odin_motor_quote_online_tour", login=None)
        comparison = self.Comparison.search([("partner_id.name", "=", "Tour Driver")])
        self.assertEqual(len(comparison), 1)
        self.assertEqual(comparison.odin_quote_type, "motor")
        order = comparison.plan_ids.order_id
        self.assertEqual(order.odin_motor_rate_id, self.definite_private)
        self.assertTrue(order.order_line.filtered(
            lambda l: l.product_id == self.env.ref("odin_motor_quote.product_motor_excess")),
            "the extra ticked on the page")
        self.assertEqual(comparison.odin_builder_params["cover"]["registration"], "KDB 456B")
