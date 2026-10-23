from odoo.tests import HttpCase, tagged

from .test_online import OnlineCase


@tagged("post_install", "-at_install")
class TestOnlineTour(OnlineCase, HttpCase):
    """A stranger on the page, in a real browser, without logging in."""

    def test_tour(self):
        self.start_tour("/medical-quote", "odin_medical_quote_online_tour", login=None)
        comparison = self.Comparison.search([("partner_id.name", "=", "Tour Visitor")])
        self.assertEqual(len(comparison), 1)
        self.assertEqual(comparison.odin_channel, "online")
        self.assertEqual(comparison.plan_ids.order_id.sale_order_template_id, self.afya)
        self.assertTrue(comparison.odin_callback_requested)
