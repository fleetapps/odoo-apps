from datetime import date, datetime

import psycopg2

from odoo import Command
from odoo.tests import TransactionCase, freeze_time, tagged
from odoo.tools import mute_logger

DAY = date(2026, 9, 27)


@tagged("post_install", "-at_install")
class TestOdinBar(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].search([("company_id", "=", cls.env.company.id)], limit=1)
        cls.store_location = cls.warehouse.lot_stock_id
        cls.bar_location = cls.env["stock.location"].create(
            {"name": "Bulls Eye (BE)", "usage": "internal", "location_id": cls.warehouse.view_location_id.id}
        )
        cls.customers = cls.env.ref("stock.stock_location_customers")
        PickingType = cls.env["stock.picking.type"]
        common = {"warehouse_id": cls.warehouse.id, "company_id": cls.env.company.id}
        cls.issue_type = PickingType.create(
            dict(common, name="Issue to BE", code="internal", sequence_code="ISS-BE",
                 default_location_src_id=cls.store_location.id, default_location_dest_id=cls.bar_location.id)
        )
        cls.return_type = PickingType.create(
            dict(common, name="Return from BE", code="internal", sequence_code="RET-BE",
                 default_location_src_id=cls.bar_location.id, default_location_dest_id=cls.store_location.id)
        )
        cls.sale_type = PickingType.create(
            dict(common, name="BE POS Sales", code="outgoing", sequence_code="SAL-BE",
                 default_location_src_id=cls.bar_location.id, default_location_dest_id=cls.customers.id)
        )
        cls.product = cls.env["product.product"].create({"name": "Jameson", "is_storable": True})
        cls.store = cls.env["odin.bar"].create(
            {"name": "Main Store", "code": "MS", "kind": "store", "location_id": cls.store_location.id,
             "tz": "Africa/Nairobi"}
        )
        cls.bar = cls.env["odin.bar"].create(
            {"name": "Bulls Eye", "code": "BE", "location_id": cls.bar_location.id, "tz": "Africa/Nairobi"}
        )

    def move(self, source, destination, qty, business_date=None):
        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": (self.sale_type if destination == self.customers else self.issue_type).id,
                "location_id": source.id,
                "location_dest_id": destination.id,
                "bar_business_date": business_date,
                "move_ids": [
                    Command.create(
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": qty,
                            "location_id": source.id,
                            "location_dest_id": destination.id,
                        }
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.move_ids.quantity = qty
        picking.move_ids.picked = True
        picking.button_validate()
        return picking

    def test_trading_day_starts_at_six(self):
        # Nairobi is UTC+3: 02:59 UTC is 05:59 at the bar.
        self.assertEqual(self.bar._business_date(datetime(2026, 9, 28, 2, 59)), DAY)
        self.assertEqual(self.bar._business_date(datetime(2026, 9, 28, 3, 0)), date(2026, 9, 28))
        self.assertEqual(self.bar._business_date(datetime(2026, 9, 27, 20, 0)), DAY)
        start, end = self.bar._business_day_bounds(DAY)
        self.assertEqual(start, datetime(2026, 9, 27, 3, 0))
        self.assertEqual(end, datetime(2026, 9, 28, 3, 0))

    def test_stock_as_of_places_pos_sales_by_trading_day(self):
        with freeze_time("2026-09-27 05:00:00"):
            self.env["stock.quant"]._update_available_quantity(self.product, self.store_location, 500)
            self.move(self.store_location, self.bar_location, 100)
        with freeze_time("2026-09-27 15:00:00"):
            self.move(self.bar_location, self.store_location, 10)  # a return at 18:00
        cutoff = datetime(2026, 9, 27, 20, 0)  # 23:00, the closing count
        with freeze_time("2026-09-28 06:00:00"):
            self.move(self.store_location, self.bar_location, 50)  # next morning's delivery
            self.move(self.bar_location, self.customers, 30, business_date=DAY)  # the day's sales
            self.move(self.bar_location, self.customers, 5, business_date=date(2026, 9, 28))
        closing = self.bar._stock_as_of(self.product, cutoff, DAY, closing=True)
        self.assertEqual(closing[self.product.id], 100 - 10 - 30)
        # Mid-shift, the day's sales posted afterwards are not known yet.
        spot = self.bar._stock_as_of(self.product, datetime(2026, 9, 27, 16, 0), DAY, closing=False)
        self.assertEqual(spot[self.product.id], 100 - 10)

    def test_pos_day_hook(self):
        self.assertFalse(self.bar._pos_day_posted(DAY))
        self.assertTrue(self.store._pos_day_posted(DAY), "stores have no POS")
        day = self.bar._mark_pos_posted(DAY, source="POS/0001")
        self.assertTrue(self.bar._pos_day_posted(DAY))
        again = self.bar._mark_pos_posted(DAY, source="POS/0002")
        self.assertEqual(day, again)
        self.assertEqual(day.source, "POS/0002")
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.env["odin.bar.pos.day"].create({"bar_id": self.bar.id, "business_date": DAY})

    def test_autofill_setup_from_stock_names(self):
        self.bar.action_autofill_setup()
        self.assertEqual(self.bar.issue_type_id, self.issue_type)
        self.assertEqual(self.bar.return_type_id, self.return_type)
        self.assertEqual(self.bar.sale_type_id, self.sale_type)
        self.assertEqual(self.bar.store_id, self.store)
        self.assertFalse(self.bar.setup_issues)
        self.store.action_autofill_setup()
        self.assertEqual(self.store.receipt_type_id, self.warehouse.in_type_id)

    def test_a_location_belongs_to_one_bar(self):
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            self.env["odin.bar"].create({"name": "Copy", "code": "CP", "location_id": self.bar_location.id})
