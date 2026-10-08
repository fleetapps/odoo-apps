"""Periods: presets, the arrows, dividing a range, comparisons."""

from datetime import date

from odoo.tests import TransactionCase, tagged

from ..models import odin_pnl_periods as periods


def calendar_year(day):
    return date(day.year, 1, 1), date(day.year, 12, 31)


def july_year(day):
    start = date(day.year if day.month >= 7 else day.year - 1, 7, 1)
    return start, date(start.year + 1, 6, 30)


@tagged("post_install", "-at_install")
class TestPeriods(TransactionCase):

    def test_presets_seen_from_a_day(self):
        today = date(2026, 10, 8)
        self.assertEqual(periods.preset_range("previous_month", today, calendar_year), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(periods.preset_range("this_quarter", today, calendar_year), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(periods.preset_range("previous_quarter", today, calendar_year), (date(2026, 7, 1), date(2026, 9, 30)))
        self.assertEqual(periods.preset_range("this_year", today, july_year), (date(2026, 7, 1), date(2027, 6, 30)))
        self.assertEqual(periods.preset_range("previous_year", today, july_year), (date(2025, 7, 1), date(2026, 6, 30)))
        self.assertEqual(periods.preset_range("year_to_date", today, july_year), (date(2026, 7, 1), today))

    def test_the_arrows_move_whole_months_by_months(self):
        # February back to January in full, and a quarter by a quarter.
        self.assertEqual(periods.shift(date(2024, 2, 1), date(2024, 2, 29), -1), (date(2024, 1, 1), date(2024, 1, 31)))
        self.assertEqual(periods.shift(date(2026, 1, 1), date(2026, 3, 31), 1), (date(2026, 4, 1), date(2026, 6, 30)))
        self.assertEqual(periods.shift(date(2026, 1, 31), date(2026, 1, 31), -1), (date(2026, 1, 30), date(2026, 1, 30)))

    def test_dividing_clips_to_the_range(self):
        self.assertEqual(
            periods.divide(date(2026, 1, 15), date(2026, 3, 10), "month"),
            [(date(2026, 1, 15), date(2026, 1, 31)), (date(2026, 2, 1), date(2026, 2, 28)), (date(2026, 3, 1), date(2026, 3, 10))])
        self.assertEqual(len(periods.divide(date(2026, 1, 1), date(2026, 12, 31), "quarter")), 4)

    def test_same_period_last_year_keeps_month_ends(self):
        self.assertEqual(
            periods.comparisons(date(2024, 2, 1), date(2024, 2, 29), "previous_year", 1),
            [(date(2023, 2, 1), date(2023, 2, 28))])
        self.assertEqual(
            periods.comparisons(date(2026, 9, 1), date(2026, 9, 30), "previous_period", 2),
            [(date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 7, 1), date(2026, 7, 31))])

    def test_labels_know_months_quarters_and_years(self):
        self.assertEqual(periods.kind_of(date(2026, 9, 1), date(2026, 9, 30), calendar_year), "month")
        self.assertEqual(periods.kind_of(date(2026, 7, 1), date(2026, 9, 30), calendar_year), "quarter")
        self.assertEqual(periods.kind_of(date(2026, 7, 1), date(2027, 6, 30), july_year), "year")
        self.assertEqual(periods.kind_of(date(2026, 9, 2), date(2026, 9, 30), calendar_year), "range")
