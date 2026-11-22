"""The page in a real browser: the view switch, the cards and the filters."""

from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestBookTour(HttpCase):

    def test_views_cards_and_filters(self):
        admin = self.env.ref("base.user_admin")
        wh = self.env["stock.warehouse"].search([("company_id", "=", admin.company_id.id)], limit=1)
        product = self.env["product.product"].create({
            "name": "Tour Handwash", "default_code": "TOUR-001", "is_storable": True, "type": "consu",
            "standard_price": 10.0,
        })
        self.env["stock.quant"]._update_available_quantity(product, wh.lot_stock_id, 5)
        # A line ready to post on the month the page opens on (the user's
        # today): the control panel at its fullest, with "Post 1 ready".
        self.env["odin.stock.line"].create({
            "date": fields.Date.context_today(self.env["odin.stock.line"].with_user(admin)),
            "product_id": product.id, "reason": "used", "qty_out": 1, "warehouse_id": wh.id,
        })
        self.start_tour("/odoo/action-odin_stock_book.odin_stock_book_action", "odin_stock_book_views_tour", login="admin")
