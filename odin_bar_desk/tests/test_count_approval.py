from datetime import date

import psycopg2

from odoo.exceptions import UserError
from odoo.tests import freeze_time, tagged
from odoo.tools import mute_logger

from .common import BarDeskCase

# Bars run on Nairobi time (UTC+3): 20:00 UTC is 23:00 at the bar.
DAY1 = date(2026, 9, 27)  # a Sunday
DAY2 = date(2026, 9, 28)


@tagged("post_install", "-at_install")
class TestCountApproval(BarDeskCase):
    def line(self, count, product):
        return count.line_ids.filtered(lambda line: line.product_id == product)

    def test_next_morning_delivery_survives_approval(self):
        """Count, then a delivery the next morning, then approval: the
        delivery is kept because only the difference is posted."""
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_store, [(self.jameson, 1000), (self.tusker, 500)])
            self.post(
                self.type_iss_be,
                self.loc_store,
                self.loc_be,
                [(self.jameson, 2, self.uom_750), (self.tusker, 48, None)],
            )
        with freeze_time("2026-09-27 20:00:00"):  # 23:00 at the bar, closing count
            count = self.submit_count(
                self.device_be,
                self.bar_be,
                self.mary,
                "1234",
                [
                    self.count_line(self.jameson, bottles={self.uom_750: 1}, open_tots=10),
                    self.count_line(self.tusker, units=40),
                ],
            )
        with freeze_time("2026-09-28 06:00:00"):  # 09:00 next morning: store delivery, then the day's POS
            token = self.login(self.device_store, self.store, self.sam, "4321")
            self.desk(self.device_store).desk_store_send(
                self.store.id,
                token,
                self.uuid(),
                self.bar_be.id,
                [
                    {"product_id": self.jameson.id, "uom_id": self.uom_1l.id, "qty": 2},
                    {"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "qty": 1},
                ],
            )
            self.pos_sales(self.bar_be, DAY1, [(self.jameson, 12), (self.tusker, 8)])
        with freeze_time("2026-09-28 07:00:00"):
            count.with_user(self.manager).action_approve()

        self.assertEqual(count.state, "approved")
        self.assertEqual(count.approved_by_id, self.manager)
        jameson = self.line(count, self.jameson)
        self.assertEqual(jameson.counted_qty, 35)
        self.assertEqual(jameson.expected_qty, 38)  # 50 in, 12 sold that day
        self.assertEqual(jameson.diff_qty, -3)
        self.assertAlmostEqual(jameson.diff_value, -240.0)
        self.assertEqual(self.line(count, self.tusker).diff_qty, 0)
        # Stock is what was counted plus what arrived after the count.
        self.assertEqual(self.qty(self.jameson, self.loc_be), 35 + 66)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 40 + 24)
        # One adjustment, for the difference, dated when the count was taken.
        self.assertEqual(len(count.move_ids), 1)
        self.assertTrue(count.move_ids.is_inventory)
        self.assertEqual(count.move_ids.quantity, 3)
        self.assertEqual(count.move_ids.location_id, self.loc_be)
        self.assertEqual(count.move_ids.date, count.submitted_at)

    def test_pos_posted_before_or_after_the_count(self):
        """The same day's sales count the same whether the POS import was
        posted before the count or the morning after."""
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_be, [(self.jameson, 100)])
            self.opening_stock(self.loc_bb, [(self.jameson, 100)])
        with freeze_time("2026-09-27 19:30:00"):  # BB's import runs at 22:30, before its count
            self.pos_sales(self.bar_bb, DAY1, [(self.jameson, 30)])
        with freeze_time("2026-09-27 20:00:00"):
            counted = [self.count_line(self.jameson, bottles={self.uom_750: 2}, open_tots=18)]
            count_be = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", counted)
            count_bb = self.submit_count(self.device_bb, self.bar_bb, self.john, "5678", counted)
        with freeze_time("2026-09-28 06:00:00"):  # BE's import runs the next morning
            self.pos_sales(self.bar_be, DAY1, [(self.jameson, 30)])
        with freeze_time("2026-09-28 07:00:00"):
            (count_be | count_bb).with_user(self.manager).action_approve()

        for count, location in ((count_be, self.loc_be), (count_bb, self.loc_bb)):
            line = self.line(count, self.jameson)
            self.assertEqual(line.expected_qty, 70)
            self.assertEqual(line.diff_qty, -2)
            self.assertEqual(self.qty(self.jameson, location), 68)

    def test_later_trading_days_are_left_out(self):
        """The next day's POS sales, even when posted before the count is
        approved, are not part of the count's expected stock."""
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_be, [(self.jameson, 100)])
        with freeze_time("2026-09-27 20:00:00"):
            count = self.submit_count(
                self.device_be, self.bar_be, self.mary, "1234", [self.count_line(self.jameson, open_tots=90)]
            )
        with freeze_time("2026-09-29 06:00:00"):
            self.pos_sales(self.bar_be, DAY1, [(self.jameson, 10)])
            self.pos_sales(self.bar_be, DAY2, [(self.jameson, 25)])
            count.with_user(self.manager).action_approve()
        self.assertEqual(self.line(count, self.jameson).expected_qty, 90)
        self.assertEqual(self.line(count, self.jameson).diff_qty, 0)
        self.assertEqual(self.qty(self.jameson, self.loc_be), 65)

    def test_spot_count_only_adjusts_counted_items(self):
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_be, [(self.jameson, 50), (self.gordons, 25), (self.tusker, 24)])
        with freeze_time("2026-09-27 15:00:00"):  # 18:00, mid-shift
            count = self.submit_count(
                self.device_be,
                self.bar_be,
                self.mary,
                "1234",
                [self.count_line(self.jameson, bottles={self.uom_750: 1}, open_tots=20)],
                kind="spot",
            )
        self.assertEqual(count.kind, "spot")
        self.assertEqual(count.line_ids.product_id, self.jameson)
        # Spot counts do not wait for the day's POS import.
        self.assertFalse(count.with_user(self.manager).approve_blocked_reason)
        with freeze_time("2026-09-27 15:30:00"):
            count.with_user(self.manager).action_approve()
        self.assertEqual(count.move_ids.product_id, self.jameson)
        self.assertEqual(self.qty(self.jameson, self.loc_be), 45)
        self.assertEqual(self.qty(self.gordons, self.loc_be), 25)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 24)

    def test_closing_approval_waits_for_the_pos_import(self):
        with freeze_time("2026-09-27 20:00:00"):
            count = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [])
        count = count.with_user(self.manager)
        message = "Post the POS import for Bulls Eye on Sun 27 Sep before approving this count."
        self.assertEqual(count.approve_blocked_reason, message)
        self.assertFalse(count.preview_ready)
        with self.assertRaisesRegex(UserError, "Post the POS import for Bulls Eye on Sun 27 Sep"):
            count.action_approve()
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        count.invalidate_recordset(["approve_blocked_reason", "preview_ready"])
        self.assertFalse(count.approve_blocked_reason)
        self.assertTrue(count.preview_ready)
        count.action_approve()
        self.assertEqual(count.state, "approved")

    def test_store_counts_need_no_pos_import(self):
        with freeze_time("2026-09-27 12:00:00"):
            self.opening_stock(self.loc_store, [(self.tusker, 100)])
            count = self.submit_count(
                self.device_store, self.store, self.sam, "4321", [self.count_line(self.tusker, units=96)]
            )
            count.with_user(self.manager).action_approve()
        self.assertEqual(self.qty(self.tusker, self.loc_store), 96)

    def test_counts_are_approved_in_order(self):
        """Approving the second day before the first is refused; approving
        both the morning after still posts each difference once."""
        self.bar_be._mark_pos_posted(DAY1)
        self.bar_be._mark_pos_posted(DAY2)
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_be, [(self.jameson, 50)])
        with freeze_time("2026-09-27 20:00:00"):
            first_day = self.submit_count(
                self.device_be, self.bar_be, self.mary, "1234", [self.count_line(self.jameson, open_tots=45)]
            )
        with freeze_time("2026-09-28 20:00:00"):
            second_day = self.submit_count(
                self.device_be, self.bar_be, self.mary, "1234", [self.count_line(self.jameson, open_tots=45)]
            )
        second_day = second_day.with_user(self.manager)
        self.assertIn(first_day.name, second_day.approve_blocked_reason)
        with self.assertRaisesRegex(UserError, "first"):
            second_day.action_approve()
        with freeze_time("2026-09-29 07:00:00"):
            first_day.with_user(self.manager).action_approve()
            second_day.invalidate_recordset(["approve_blocked_reason"])
            second_day.action_approve()
        self.assertEqual(self.line(first_day, self.jameson).diff_qty, -5)
        self.assertEqual(self.line(second_day, self.jameson).expected_qty, 45)
        self.assertEqual(self.line(second_day, self.jameson).diff_qty, 0)
        self.assertEqual(self.qty(self.jameson, self.loc_be), 45)

    def test_one_approved_closing_count_per_bar_and_day(self):
        self.bar_be._mark_pos_posted(DAY1)
        with freeze_time("2026-09-27 20:00:00"):
            first = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [])
        with freeze_time("2026-09-27 20:30:00"):
            second = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [])
        # The new closing count replaces the one still waiting.
        self.assertEqual(first.state, "cancel")
        self.assertEqual(first.replaced_by_id, second)
        second.with_user(self.manager).action_approve()
        with freeze_time("2026-09-27 21:00:00"):
            token = self.login(self.device_be, self.bar_be, self.mary, "1234")
            with self.assertRaisesRegex(UserError, "already approved"):
                self.desk(self.device_be).desk_count_start(self.bar_be.id, token)
        # The database refuses a second approved closing count even if forced.
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
            first.sudo().write({"state": "approved"})
            first.flush_recordset()

    def test_recount_is_replaced_by_the_new_count(self):
        with freeze_time("2026-09-27 20:00:00"):
            first = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [])
            first.with_user(self.manager).action_request_recount()
            self.assertEqual(first.state, "recount")
            token = self.login(self.device_be, self.bar_be, self.mary, "1234")
            home = self.desk(self.device_be).desk_home(self.bar_be.id, token)
            self.assertEqual(home["count"]["recounts"][0]["business_date"], "2026-09-27")
        with freeze_time("2026-09-27 21:00:00"):
            second = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [])
        self.assertEqual(first.state, "cancel")
        self.assertEqual(first.replaced_by_id, second)
        self.assertEqual(second.state, "submitted")

    def test_closing_count_needs_every_line(self):
        with freeze_time("2026-09-27 20:00:00"):
            token = self.login(self.device_be, self.bar_be, self.mary, "1234")
            desk = self.desk(self.device_be)
            data = desk.desk_count_start(self.bar_be.id, token)
            self.assertEqual(len(data["lines"]), 3)
            with self.assertRaisesRegex(UserError, "2 items are not counted yet"):
                desk.desk_count_submit(
                    self.bar_be.id, token, self.uuid(), data["id"], [self.count_line(self.jameson, open_tots=0)]
                )
            # An explicit zero counts as counted.
            desk.desk_count_submit(
                self.bar_be.id,
                token,
                self.uuid(),
                data["id"],
                [self.count_line(self.gordons), self.count_line(self.tusker, units=0)],
            )
        self.assertEqual(self.env["odin.bar.count"].browse(data["id"]).state, "submitted")

    def test_count_is_blind_and_resumable(self):
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_be, [(self.jameson, 50)])
        with freeze_time("2026-09-27 20:00:00"):
            token = self.login(self.device_be, self.bar_be, self.mary, "1234")
            desk = self.desk(self.device_be)
            data = desk.desk_count_start(self.bar_be.id, token)
            self.assertEqual(data["day_label"], "Sun 27 Sep")
            self.assertNotIn("expected", str(data))
            desk.desk_count_save(
                self.bar_be.id, token, data["id"], [self.count_line(self.jameson, bottles={self.uom_750: 1})]
            )
            # Reopening the Desk resumes the same draft with what was saved.
            again = desk.desk_count_start(self.bar_be.id, token)
            self.assertEqual(again["id"], data["id"])
            jameson = next(line for line in again["lines"] if line["product_id"] == self.jameson.id)
            self.assertEqual(jameson["bottle_detail"], {str(self.uom_750.id): 1.0})
            # Starting over drops the draft.
            fresh = desk.desk_count_start(self.bar_be.id, token, restart=True)
            self.assertNotEqual(fresh["id"], data["id"])
            self.assertEqual(self.env["odin.bar.count"].browse(data["id"]).state, "cancel")

    def test_counts_before_six_belong_to_the_previous_day(self):
        with freeze_time("2026-09-27 23:30:00"):  # 02:30 on the 28th at the bar
            token = self.login(self.device_be, self.bar_be, self.mary, "1234")
            data = self.desk(self.device_be).desk_count_start(self.bar_be.id, token)
        self.assertEqual(data["business_date"], "2026-09-27")
        self.assertEqual(data["day_label"], "Sun 27 Sep")

    def test_full_bottles_of_several_sizes(self):
        with freeze_time("2026-09-27 20:00:00"):
            count = self.submit_count(
                self.device_be,
                self.bar_be,
                self.mary,
                "1234",
                [self.count_line(self.jameson, bottles={self.uom_750: 2, self.uom_1l: 1}, open_tots=5)],
            )
        line = self.line(count, self.jameson)
        self.assertEqual(line.full_bottles, 3)
        self.assertEqual(line.full_qty, 83)
        self.assertEqual(line.counted_qty, 88)
        self.assertEqual(line.counted_display, "2 × Bottle 750ml (25 tots) + 1 × Bottle 1L (33 tots) + 5 open")
