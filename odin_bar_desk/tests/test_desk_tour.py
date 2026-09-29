from odoo.tests import HttpCase, new_test_user, tagged

from .common import BarDeskCase


@tagged("post_install", "-at_install")
class TestDeskTour(BarDeskCase, HttpCase):
    """The Desk in a real browser, on the bar tablet's own login."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # start_tour signs in with the login as password.
        cls.tablet = new_test_user(
            cls.env,
            login="bulls.eye.tablet",
            groups="odin_bar_desk.group_bar_desk_staff",
            odin_bar_id=cls.bar_be.id,
            action_id=cls.env.ref("odin_bar_desk.action_bar_desk").id,
            tz="Africa/Nairobi",
        )
        cls.opening_stock(cls.loc_be, [(cls.jameson, 50), (cls.tusker, 48)])

    def test_stock_out_tour(self):
        self.start_tour("/odoo", "odin_bar_desk_stock_out", login="bulls.eye.tablet")
        scrap = self.env["stock.scrap"].search([("bar_employee_id", "=", self.mary.id)])
        self.assertEqual(scrap.product_id, self.tusker)
        self.assertEqual(scrap.scrap_qty, 2)
        self.assertEqual(scrap.state, "done")

    def test_count_tour(self):
        self.start_tour("/odoo", "odin_bar_desk_count", login="bulls.eye.tablet")
        count = self.env["odin.bar.count"].search([("bar_id", "=", self.bar_be.id)])
        self.assertEqual(count.state, "submitted")
        self.assertEqual(count.employee_id, self.mary)
        counted = {line.product_id: line.counted_qty for line in count.line_ids}
        self.assertEqual(counted, {self.jameson: 37, self.tusker: 27, self.gordons: 0})
