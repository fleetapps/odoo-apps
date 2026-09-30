from datetime import date

from odoo.exceptions import UserError
from odoo.tests import freeze_time, tagged

from .common import BarDeskCase

# Bars run on Nairobi time (UTC+3). The controller closes a trading day the
# next morning: 07:00 UTC is 10:00 at the club.
DAY0 = date(2026, 9, 26)
DAY1 = date(2026, 9, 27)


@tagged("post_install", "-at_install")
class TestStockController(BarDeskCase):
    """One person logs the moves, counts every location the next morning
    against the stock expected, explains the differences and approves."""

    def setUp(self):
        super().setUp()
        self.desk_ = self.desk(self.controller)
        with freeze_time("2026-09-26 05:00:00"):
            self.opening_stock(self.loc_store, [(self.jameson, 1000), (self.tusker, 480)])
            self.opening_stock(self.loc_be, [(self.jameson, 100), (self.tusker, 48)])
            self.opening_stock(self.loc_bb, [(self.tusker, 24)])

    def token(self):
        return self.controller_token()

    def move(self, token, source, dest, lines, business_date=False):
        return self.desk_.desk_move(
            self.store.id,
            token,
            self.uuid(),
            source.id,
            [{"product_id": product.id, "uom_id": uom.id, "qty": qty} for product, uom, qty in lines],
            to_bar_id=dest.id,
            business_date=business_date and str(business_date),
        )

    def count(self, bar, lines, day=DAY1):
        return self.submit_count(self.controller, bar, self.kim, "9999", lines, day)

    def line(self, count, product):
        return count.line_ids.filtered(lambda line: line.product_id == product)

    def test_expected_stock_is_explained(self):
        """Opening (the last count) + moved - sold = expected."""
        for day in (DAY0, DAY1):
            self.bar_bb._mark_pos_posted(day, no_sales=True)
        self.bar_be._mark_pos_posted(DAY0, no_sales=True)
        with freeze_time("2026-09-27 07:00:00"):  # Sunday 10:00: Saturday's count
            saturday = self.count(self.bar_be, [self.count_line(self.jameson, open_tots=100)], DAY0)
            self.approve(saturday)
        with freeze_time("2026-09-27 09:00:00"):  # Sunday noon: two bottles from the store
            self.move(self.token(), self.store, self.bar_be, [(self.jameson, self.uom_750, 2)])
        with freeze_time("2026-09-28 06:00:00"):  # Monday 09:00: the POS report, then the paper sheet
            self.pos_sales(self.bar_be, DAY1, [(self.jameson, 30)])
            self.move(self.token(), self.bar_be, self.bar_bb, [(self.jameson, self.uom_tot, 10)], DAY1)
        with freeze_time("2026-09-28 07:00:00"):
            data = self.desk_.desk_count_start(self.bar_be.id, self.token(), str(DAY1))
        jameson = next(line for line in data["lines"] if line["product_id"] == self.jameson.id)
        self.assertEqual(
            {key: jameson[key] for key in ("opening", "moved", "sold", "expected")},
            {"opening": 100, "moved": 40, "sold": 30, "expected": 110},
        )
        self.assertFalse(data["pos_missing_label"])
        # The move logged for Sunday is not a sale at Bulls Eye.
        self.assertEqual(self.bar_be._pos_moved_qty(self.jameson, DAY1)[self.jameson.id], 30)
        pos_day = self.env["odin.bar.pos.day"].search([("bar_id", "=", self.bar_be.id), ("business_date", "=", DAY1)])
        self.assertFalse(pos_day.picking_ids.filtered("bar_manual_move"))
        # Approval keeps the breakdown with the line.
        with freeze_time("2026-09-28 07:30:00"):
            count = self.count(self.bar_be, [self.count_line(self.jameson, open_tots=105)])
            self.approve(count, "spillage")
        line = self.line(count, self.jameson)
        self.assertEqual((line.opening_qty, line.moved_qty, line.sold_qty), (100, 40, 30))
        self.assertEqual((line.expected_qty, line.diff_qty), (110, -5))
        self.assertEqual(line.variance_reason_id.code, "spillage")
        self.assertEqual(count.move_ids.bar_variance_reason_id.code, "spillage")

    def test_a_morning_move_logged_before_the_count(self):
        """Stock the store sends out before a bar is counted is at the bar
        when it is counted: logged on the day being closed, the count
        expects it."""
        with freeze_time("2026-09-28 06:30:00"):
            self.move(self.token(), self.store, self.bar_be, [(self.tusker, self.uom_crate, 1)], DAY1)
        with freeze_time("2026-09-28 07:00:00"):
            count = self.count(self.bar_be, [self.count_line(self.tusker, units=72), self.count_line(self.jameson, open_tots=100)])
        self.assertFalse(count._unresolved_lines())

    def test_every_difference_needs_a_reason(self):
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        with freeze_time("2026-09-28 07:00:00"):
            count = self.count(
                self.bar_be, [self.count_line(self.jameson, open_tots=95), self.count_line(self.tusker, units=48)]
            )
            token = self.token()
        self.assertEqual(self.line(count, self.jameson), count._unresolved_lines())
        with self.assertRaisesRegex(UserError, "give a reason for 1 difference"):
            count.with_user(self.manager).action_approve()
        breakage = self.env["odin.bar.variance.reason"].search([("code", "=", "breakage")])
        with freeze_time("2026-09-28 07:05:00"):
            self.desk_.desk_explain(self.store.id, token, self.line(count, self.jameson).id, breakage.id, " dropped ")
        self.assertEqual(self.line(count, self.jameson).variance_reason_id, breakage)
        self.assertEqual(self.line(count, self.jameson).variance_note, "dropped")
        activity = self.env["odin.bar.activity"].search([("kind", "=", "resolve")])
        self.assertEqual((activity.bar_id, activity.employee_id), (self.bar_be, self.kim))
        count.with_user(self.manager).action_approve()
        self.assertEqual(self.qty(self.jameson, self.loc_be), 95)

    def test_missed_move_suggestion_and_count_correction(self):
        with freeze_time("2026-09-28 07:00:00"):
            token = self.token()
            be = self.count(self.bar_be, [self.count_line(self.jameson, open_tots=90), self.count_line(self.tusker, units=24)])
            bb = self.count(self.bar_bb, [self.count_line(self.tusker, units=48)])
            data = self.desk_.desk_differences(self.store.id, token)
        diffs = {(line["bar"]["code"], line["product_id"]): line["diff"] for line in data["lines"]}
        self.assertEqual(diffs, {("BE", self.jameson.id): -10, ("BE", self.tusker.id): -24, ("BB", self.tusker.id): 24})
        self.assertEqual(
            data["suggestions"],
            [
                {
                    "product_id": self.tusker.id,
                    "qty": 24,
                    "from": {"id": self.bar_be.id, "name": "Bulls Eye", "code": "BE"},
                    "to": {"id": self.bar_bb.id, "name": "Banda Bar", "code": "BB"},
                }
            ],
        )
        with freeze_time("2026-09-28 07:10:00"):
            self.move(token, self.bar_be, self.bar_bb, [(self.tusker, self.uom_unit, 24)], DAY1)
            # The Jameson line was miscounted: 100, not 90.
            jameson = self.line(be, self.jameson)
            jameson.variance_reason_id = self.env["odin.bar.variance.reason"].search([("code", "=", "staff")])
            self.desk_.desk_count_fix(self.store.id, token, jameson.id, {"open_tots": 100})
            data = self.desk_.desk_differences(self.store.id, token)
        self.assertEqual(data["lines"], [])
        self.assertEqual(data["suggestions"], [])
        self.assertTrue(jameson.recounted)
        self.assertFalse(jameson.variance_reason_id)
        self.assertEqual(jameson.counted_qty, 100)
        self.assertFalse(be._unresolved_lines() | bb._unresolved_lines())

    def test_approve_the_day(self):
        for bar in (self.bar_be, self.bar_bb):
            bar._mark_pos_posted(DAY1, no_sales=True)
        with freeze_time("2026-09-28 07:00:00"):
            token = self.token()
            self.count(self.store, [self.count_line(self.jameson, open_tots=1000), self.count_line(self.tusker, units=480)])
            be = self.count(self.bar_be, [self.count_line(self.jameson, open_tots=100), self.count_line(self.tusker, units=46)])
            with self.assertRaisesRegex(UserError, "Count Banda Bar first"):
                self.desk_.desk_approve_day(self.store.id, token, self.uuid(), str(DAY1))
            self.count(self.bar_bb, [self.count_line(self.tusker, units=24)])
            home = self.desk_.desk_home(self.store.id, token)
            self.assertEqual((home["counted"], home["differences"], home["unresolved"]), (3, 1, 1))
            self.assertFalse(home["can_approve"])
            with self.assertRaisesRegex(UserError, "give a reason"):
                self.desk_.desk_approve_day(self.store.id, token, self.uuid(), str(DAY1))
            reason = self.env["odin.bar.variance.reason"].search([("code", "=", "breakage")])
            self.desk_.desk_explain(self.store.id, token, self.line(be, self.tusker).id, reason.id)
            home = self.desk_.desk_home(self.store.id, token)
            self.assertTrue(home["can_approve"], home["approve_blocked"])
            request = self.uuid()
            result = self.desk_.desk_approve_day(self.store.id, token, request, str(DAY1))
            again = self.desk_.desk_approve_day(self.store.id, token, request, str(DAY1))
        self.assertEqual(result["activity_id"], again["activity_id"])
        counts = self.env["odin.bar.count"].search([("business_date", "=", DAY1), ("state", "!=", "cancel")])
        self.assertEqual(set(counts.mapped("state")), {"approved"})
        self.assertEqual(counts.approved_by_id, self.controller)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 46)
        self.assertEqual(be.move_ids.bar_variance_reason_id, reason)
        self.assertAlmostEqual(result["variance"], -300.0)
        with freeze_time("2026-09-28 07:30:00"):
            home = self.desk_.desk_home(self.store.id, token)
            self.assertTrue(home["approved"])
            self.assertEqual(home["days"][2]["state"], "approved")
            with self.assertRaisesRegex(UserError, "already approved"):
                self.desk_.desk_approve_day(self.store.id, token, self.uuid(), str(DAY1))

    def test_stock_levels_against_par(self):
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
        levels = self.desk_.desk_stock_levels(self.store.id, self.token())
        self.assertEqual([loc["code"] for loc in levels["locations"]], ["MS", "BB", "BE"])
        rows = {row["product_id"]: row for row in levels["rows"]}
        self.assertEqual(rows[self.tusker.id]["total"], 480 + 48 + 24)
        self.assertEqual(rows[self.tusker.id]["qty"][str(self.bar_bb.id)], 24)
        self.assertEqual(rows[self.tusker.id]["par"], 600)
        self.assertEqual(rows[self.jameson.id]["par"], 0)
        self.assertNotIn(self.gordons.id, rows)

    def test_dashboard(self):
        with freeze_time("2026-09-28 07:00:00"):
            self.count(self.bar_be, [self.count_line(self.jameson, open_tots=90), self.count_line(self.tusker, units=48)])
        with freeze_time("2026-09-28 07:30:00"):  # after the count: not part of it
            self.move(self.token(), self.store, self.bar_be, [(self.tusker, self.uom_crate, 1)])
            bar = self.bar_be.with_user(self.manager)
            self.assertEqual((bar.desk_to_approve_count, bar.desk_unresolved_count, bar.desk_moves_today), (1, 1, 1))

    def test_reports(self):
        """Stock by location and its history agree with the Desk."""
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        with freeze_time("2026-09-28 06:00:00"):
            self.move(self.token(), self.store, self.bar_be, [(self.tusker, self.uom_crate, 1)], DAY1)
        with freeze_time("2026-09-28 07:00:00"):
            count = self.count(self.bar_be, [self.count_line(self.jameson, open_tots=100), self.count_line(self.tusker, units=70)])
        with freeze_time("2026-09-28 22:00:00"):  # after midnight at the club: a new trading day
            self.approve(count, "breakage")
        self.env.flush_all()
        Stock = self.env["odin.bar.stock.report"]
        tusker_be = Stock.search([("bar_id", "=", self.bar_be.id), ("product_id", "=", self.tusker.id)])
        self.assertEqual(tusker_be.quantity, 70)
        self.assertEqual(tusker_be.bulk_uom_id, self.uom_crate)
        self.assertAlmostEqual(tusker_be.bulk_qty, 70 / 24, places=2)
        self.assertAlmostEqual(tusker_be.value, 70 * 150.0)
        jameson_be = Stock.search([("bar_id", "=", self.bar_be.id), ("product_id", "=", self.jameson.id)])
        self.assertEqual((jameson_be.quantity, jameson_be.bulk_uom_id, jameson_be.bulk_qty), (100, self.uom_750, 4))
        self.assertEqual(
            Stock.search([("bar_id", "=", self.store.id), ("product_id", "=", self.tusker.id)]).quantity, 480 - 24
        )
        History = self.env["odin.bar.stock.history"]
        rows = History.search([("bar_id", "=", self.bar_be.id), ("product_id", "=", self.tusker.id)])
        by_kind = {(row.kind, row.direction): (row.quantity, row.business_date) for row in rows}
        self.assertEqual(by_kind[("move", "in")], (24, DAY1))
        self.assertEqual(by_kind[("count", "out")], (-2, DAY1))
        self.assertEqual(by_kind[("adjustment", "in")], (48, DAY0))
        self.assertEqual(sum(rows.mapped("quantity")), 70)
        count_row = rows.filtered(lambda row: row.kind == "count")
        self.assertEqual(count_row.variance_reason_id.code, "breakage")
        self.assertEqual(count_row.count_id, count)
        store_out = History.search([("bar_id", "=", self.store.id), ("kind", "=", "move")])
        self.assertEqual((store_out.direction, store_out.quantity, store_out.employee_id), ("out", -24, self.kim))
