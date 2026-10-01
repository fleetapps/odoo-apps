from datetime import date

from odoo.exceptions import AccessError
from odoo.tests import freeze_time, tagged

from .common import BarDeskCase

DAY1 = date(2026, 9, 27)
# Monday 10:00 at the club: Sunday 27 is the day being closed.
MONDAY = "2026-09-28 07:00:00"


@tagged("post_install", "-at_install")
class TestDashboard(BarDeskCase):
    """The managers' dashboard: closing grid, locations, variance, stock and
    what left the club, for every location or one."""

    def setUp(self):
        super().setUp()
        self.desk_ = self.desk(self.controller)
        self.dashboard = self.env["odin.bar.dashboard"].with_user(self.controller)
        with freeze_time("2026-09-26 05:00:00"):
            self.opening_stock(self.loc_store, [(self.jameson, 1000), (self.tusker, 480)])
            self.opening_stock(self.loc_be, [(self.jameson, 100), (self.tusker, 48)])
            self.opening_stock(self.loc_bb, [(self.tusker, 24)])

    def count(self, bar, lines):
        return self.submit_count(self.controller, bar, self.kim, "9999", lines, DAY1)

    def data(self, period=3, bar=False):
        with freeze_time(MONDAY):
            return self.dashboard.dashboard_data(period, bar and bar.id)

    def test_closing_grid_and_what_is_left(self):
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        with freeze_time(MONDAY):
            self.count(self.bar_be, [self.count_line(self.jameson, open_tots=90), self.count_line(self.tusker, units=48)])
        data = self.data()
        closing = data["closing"]
        self.assertEqual([day["date"] for day in closing["days"]], ["2026-09-25", "2026-09-26", "2026-09-27"])
        self.assertEqual([row["bar"]["code"] for row in closing["rows"]], ["MS", "BB", "BE"])
        cells = {row["bar"]["code"]: row["cells"][-1] for row in closing["rows"]}
        self.assertEqual((cells["BE"]["state"], cells["BE"]["unresolved"]), ("submitted", 1))
        self.assertFalse(cells["BE"]["pos_missing"])
        self.assertEqual(cells["BB"]["state"], "none")
        self.assertTrue(cells["BB"]["pos_missing"])
        # Days before the first count ever are not "never closed".
        self.assertEqual(closing["days"][0]["state"], "before")
        self.assertEqual(closing["days"][-1]["state"], "open")
        self.assertFalse(closing["missed"])
        focus = closing["focus"]
        self.assertEqual(focus["date"], "2026-09-27")
        self.assertFalse(focus["ready"])
        self.assertIn({"bar": "BE", "text": "1 to explain", "tone": "attention"}, focus["items"])
        self.assertIn({"bar": "BB", "text": "not counted", "tone": "attention"}, focus["items"])
        self.assertIn({"bar": "BB", "text": "no POS", "tone": "attention"}, focus["items"])
        # Filtered to one location, the grid keeps only its row.
        rows = self.data(bar=self.bar_be)["closing"]["rows"]
        self.assertEqual([row["bar"]["code"] for row in rows], ["BE"])

    def test_variance_by_reason_location_and_product(self):
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        with freeze_time(MONDAY):
            count = self.count(self.bar_be, [self.count_line(self.jameson, open_tots=90), self.count_line(self.tusker, units=48)])
            self.approve(count, "breakage")
        line = count.line_ids.filtered(lambda line: line.product_id == self.jameson)
        lost = round(-line.diff_value)
        self.assertGreater(lost, 0)
        data = self.data()
        variance = data["variance"]
        self.assertEqual(variance["short"], lost)
        self.assertEqual(variance["over"], 0)
        self.assertEqual([(row["name"], row["value"]) for row in variance["reasons"]], [("Breakage", lost)])
        self.assertFalse(variance["reasons"][0]["unexplained"])
        self.assertEqual(variance["locations"][0]["code"], "BE")
        self.assertEqual(variance["locations"][0]["value"], lost)
        self.assertEqual(variance["products"][0]["id"], self.jameson.id)
        card = next(card for card in data["locations"] if card["bar"]["code"] == "BE")
        self.assertEqual((card["short"], card["top_reason"]["name"]), (lost, "Breakage"))
        self.assertEqual(card["last_count"]["state"], "approved")
        self.assertEqual(data["closing"]["rows"][2]["cells"][-1]["state"], "approved")
        # Another location shows none of it.
        self.assertEqual(self.data(bar=self.bar_bb)["variance"]["short"], 0)

    def test_moves_stock_and_out_of_the_club(self):
        self.env["stock.warehouse.orderpoint"].create(
            {
                "product_id": self.tusker.id,
                "location_id": self.loc_store.id,
                "warehouse_id": self.warehouse.id,
                "product_min_qty": 600,
                "product_max_qty": 600,
                "trigger": "manual",
            }
        )
        with freeze_time("2026-09-27 09:00:00"):
            token = self.controller_token()
            self.desk_.desk_move(
                self.store.id, token, self.uuid(), self.store.id,
                [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 1}], to_bar_id=self.bar_be.id,
            )
            self.desk_.desk_move(
                self.store.id, token, self.uuid(), self.bar_be.id,
                [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 2}],
                reason_id=self.reasons["debt"].id, member_ref="J. Kamau",
            )
            self.desk_.desk_move(
                self.store.id, token, self.uuid(), self.bar_be.id,
                [{"product_id": self.tusker.id, "uom_id": self.uom_unit.id, "qty": 6}],
                reason_id=self.reasons["roma"].id,
            )
        data = self.data()
        cards = {card["bar"]["code"]: card for card in data["locations"]}
        self.assertEqual((cards["MS"]["moves_out"], cards["BE"]["moves_in"]), (1, 1))
        self.assertEqual(cards["BE"]["moves_out"], 2)
        out = data["out"]
        self.assertEqual({row["name"]: row["count"] for row in out["reasons"]}, {"Unpaid bill": 1, "Roma": 1})
        self.assertEqual(out["chase_count"], 1)
        self.assertEqual(out["chase"][0]["member"], "J. Kamau")
        # Stock: 480 - 24 at the store, 48 + 24 - 8 at BE, 24 at BB = 544 < 600.
        stock = data["stock"]
        self.assertEqual(stock["below_count"], 1)
        self.assertEqual(stock["below"][0]["id"], self.tusker.id)
        self.assertEqual(stock["total"], sum(row["value"] for row in stock["values"]))
        # The store's moves out of the club are not Bulls Eye's.
        self.assertFalse(self.data(bar=self.bar_bb)["out"]["reasons"])

    def test_open_and_access(self):
        with freeze_time(MONDAY):
            for what in ("count", "variance", "stock", "history", "out"):
                action = self.dashboard.dashboard_open(what, False, "2026-09-25", "2026-09-27")
                self.assertEqual(action["type"], "ir.actions.act_window", what)
            desk = self.dashboard.dashboard_open("desk", False, "2026-09-27")
            self.assertEqual((desk["tag"], desk["context"]["bar_desk_day"]), ("bar_desk", "2026-09-27"))
        staff = self.env["odin.bar.dashboard"].with_user(self.device_be)
        with self.assertRaises(AccessError):
            staff.dashboard_data(7)
