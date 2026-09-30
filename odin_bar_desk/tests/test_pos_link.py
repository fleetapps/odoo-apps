from datetime import date

from odoo.exceptions import UserError
from odoo.tests import freeze_time, tagged

from .common import BarDeskCase

# Bars run on Nairobi time (UTC+3): 20:00 UTC is 23:00 at the bar.
DAY1 = date(2026, 9, 27)  # a Sunday
DAY2 = date(2026, 9, 28)
DAY3 = date(2026, 9, 29)


@tagged("post_install", "-at_install")
class TestPosLink(BarDeskCase):
    """Counts and the POS days a POS importer posts through odin_bar_base."""

    def line(self, count, product):
        return count.line_ids.filtered(lambda line: line.product_id == product)

    def test_counts_wait_for_every_earlier_pos_day(self):
        """Sales of a day imported late would change the stock under a later
        count, so a count waits for every day since the bar's first POS day."""
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        with freeze_time("2026-09-29 20:00:00"):
            count = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [], DAY3)
        self.assertEqual(count.business_date, DAY3)
        self.bar_be._mark_pos_posted(DAY3, no_sales=True)
        count = count.with_user(self.manager)
        self.assertEqual(
            count.approve_blocked_reason,
            "Post the POS import for Bulls Eye on Mon 28 Sep before approving this count.",
        )
        self.assertEqual(count.pos_missing_date, DAY2)
        self.assertFalse(count.pos_import_available, "no POS importer to open here")
        with self.assertRaisesRegex(UserError, "Mon 28 Sep"):
            count.action_approve()
        self.bar_be._mark_pos_posted(DAY2, no_sales=True)
        count.invalidate_recordset(["approve_blocked_reason", "pos_missing_date"])
        self.assertFalse(count.approve_blocked_reason)
        with self.assertRaisesRegex(UserError, "already posted"):
            count.action_open_pos_import()
        count.action_approve()
        self.assertEqual(count.state, "approved")

    def test_undo_sends_approved_counts_back(self):
        """A POS day undone after its counts were approved: they go back for
        approval with their adjustments reversed, and approving them again
        after the corrected import settles on the new figures."""
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_be, [(self.jameson, 100)])
        with freeze_time("2026-09-27 20:00:00"):
            day1 = self.submit_count(
                self.device_be, self.bar_be, self.mary, "1234", [self.count_line(self.jameson, open_tots=68)], DAY1
            )
        with freeze_time("2026-09-28 06:00:00"):
            # The import says 30 tots were sold; in truth it was 32.
            wrong = self.pos_sales(self.bar_be, DAY1, [(self.jameson, 30)])
            self.approve(day1)
        self.assertEqual(self.line(day1, self.jameson).diff_qty, -2)
        with freeze_time("2026-09-28 20:00:00"):
            day2 = self.submit_count(
                self.device_be, self.bar_be, self.mary, "1234", [self.count_line(self.jameson, open_tots=60)], DAY2
            )
        with freeze_time("2026-09-29 06:00:00"):
            self.pos_sales(self.bar_be, DAY2, [(self.jameson, 8)])
            day2.with_user(self.manager).action_approve()
        self.assertEqual(self.line(day2, self.jameson).diff_qty, 0)
        self.assertEqual(self.qty(self.jameson, self.loc_be), 60)

        # What the import screen warns about before undoing.
        dependents = self.bar_be._pos_day_dependents(DAY1)
        self.assertEqual([link["id"] for link in dependents], (day1 | day2).ids)
        self.assertEqual(dependents[0]["model"], "odin.bar.count")
        with freeze_time("2026-09-29 07:00:00"):
            self.undo_pos_sales(self.bar_be, DAY1, wrong)
        for count in (day1, day2):
            self.assertEqual(count.state, "submitted")
            self.assertFalse(count.approved_by_id)
            self.assertTrue(any("Back to approval" in body for body in count.message_ids.mapped("body")))
        self.assertFalse(self.line(day1, self.jameson).diff_qty)
        # Stock without the day's sales and without the reversed adjustment.
        self.assertEqual(self.qty(self.jameson, self.loc_be), 60 + 30 + 2)
        self.assertIn("Sun 27 Sep", day1.with_user(self.manager).approve_blocked_reason)

        with freeze_time("2026-09-29 08:00:00"):
            self.pos_sales(self.bar_be, DAY1, [(self.jameson, 32)])
            (day1 | day2).with_user(self.manager).action_approve()
        self.assertEqual(self.line(day1, self.jameson).expected_qty, 68)
        self.assertEqual(self.line(day1, self.jameson).diff_qty, 0)
        self.assertEqual(self.line(day2, self.jameson).diff_qty, 0)
        self.assertEqual(self.qty(self.jameson, self.loc_be), 60)

    def test_sales_found_for_a_day_without_sales(self):
        """A day marked "no sales" whose sales turn up after its count was
        approved: the count goes back for approval."""
        with freeze_time("2026-09-27 05:00:00"):
            self.opening_stock(self.loc_be, [(self.tusker, 48)])
        with freeze_time("2026-09-27 20:00:00"):
            count = self.submit_count(
                self.device_be, self.bar_be, self.mary, "1234", [self.count_line(self.tusker, units=40)], DAY1
            )
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        self.approve(count, "breakage")
        self.assertEqual(self.line(count, self.tusker).diff_qty, -8)
        breakage = self.env["stock.move"].search([("bar_count_id", "=", count.id)])
        self.assertEqual(breakage.bar_variance_reason_id.code, "breakage")
        with freeze_time("2026-09-28 06:00:00"):
            self.pos_sales(self.bar_be, DAY1, [(self.tusker, 8)])
        self.assertEqual(count.state, "submitted")
        count.with_user(self.manager).action_approve()
        self.assertEqual(self.line(count, self.tusker).diff_qty, 0)
        self.assertEqual(self.qty(self.tusker, self.loc_be), 40)

    def test_links_for_the_import_screen(self):
        with freeze_time("2026-09-27 20:00:00"):
            count = self.submit_count(self.device_be, self.bar_be, self.mary, "1234", [], DAY1)
        self.assertFalse(self.bar_be._pos_day_waiting(DAY1))
        self.bar_be._mark_pos_posted(DAY1, no_sales=True)
        self.assertEqual(
            self.bar_be._pos_day_waiting(DAY1),
            [{"model": "odin.bar.count", "id": count.id, "name": count.name, "extra": "ready to approve"}],
        )
        self.assertFalse(self.bar_be._pos_day_dependents(DAY1))
        count.with_user(self.manager).action_approve()
        self.assertFalse(self.bar_be._pos_day_waiting(DAY1))
        self.assertEqual([link["id"] for link in self.bar_be._pos_day_dependents(DAY1)], count.ids)
        self.assertFalse(self.bar_bb._pos_day_dependents(DAY1))

    def test_dashboard_shows_the_missing_pos_day(self):
        with freeze_time("2026-09-29 10:00:00"):  # 13:00 on Tue 29 Sep
            self.bar_be._mark_pos_posted(DAY1, no_sales=True)
            bar = self.bar_be.with_user(self.manager)
            self.assertEqual(bar.desk_pos_missing_date, DAY2)
            self.bar_be._mark_pos_posted(DAY2, no_sales=True)
            bar.invalidate_recordset(["desk_pos_missing_date"])
            self.assertFalse(bar.desk_pos_missing_date)
            self.assertEqual(bar.desk_last_pos_date, DAY2)
            self.assertFalse(self.store.with_user(self.manager).desk_pos_missing_date)
