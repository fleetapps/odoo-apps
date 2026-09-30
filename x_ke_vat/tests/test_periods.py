# -*- coding: utf-8 -*-
"""The previous return period, with and without seeded statutory periods.

The seeded ``date.range`` records are the deployment's, and only the range that
ends the day before a period starts may stand in for "the previous period".
The bake seeds a bounded window of months and never re-seeds, so the newest
range eventually lies behind the period being filed; the day that happens,
"the latest range before this one" would silently be months old, and box 19
would read its credit from the wrong month.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from .common import KeVatCase


@tagged("post_install_l10n", "post_install", "-at_install")
class TestPreviousPeriod(KeVatCase):

    def _seed(self, *months):
        """Seed 'Kenya VAT Period' ranges for the given (year, month) pairs,
        or skip when the OCA date_range module is not on this database."""
        if "date.range" not in self.env:
            self.skipTest("date_range is not installed; only the fallback applies")
        range_type = self.env["date.range.type"].create({
            "name": "Kenya VAT Period",
            "company_id": self.ke_company.id,
        })
        for year, month in months:
            start = fields.Date.to_date("%04d-%02d-01" % (year, month))
            self.env["date.range"].create({
                "name": "%04d-%02d" % (year, month),
                "date_start": start,
                "date_end": start + relativedelta(months=1, days=-1),
                "type_id": range_type.id,
                "company_id": self.ke_company.id,
            })

    def test_without_seeded_periods_the_previous_month_is_calendar_arithmetic(self):
        self.assertEqual(
            self.ke_company._ke_previous_period(
                fields.Date.to_date("2026-09-01"), fields.Date.to_date("2026-09-30")),
            (fields.Date.to_date("2026-08-01"), fields.Date.to_date("2026-08-31")))

    def test_an_adjacent_seeded_period_is_used(self):
        self._seed((2026, 7), (2026, 8))
        self.assertEqual(
            self.ke_company._ke_previous_period(
                fields.Date.to_date("2026-09-01"), fields.Date.to_date("2026-09-30")),
            (fields.Date.to_date("2026-08-01"), fields.Date.to_date("2026-08-31")))

    def test_a_stale_seeded_period_is_ignored_not_returned(self):
        # Seeding stopped in March: the deployment's window has run out. The
        # previous period of September is still August, not March.
        self._seed((2026, 2), (2026, 3))
        self.assertEqual(
            self.ke_company._ke_previous_period(
                fields.Date.to_date("2026-09-01"), fields.Date.to_date("2026-09-30")),
            (fields.Date.to_date("2026-08-01"), fields.Date.to_date("2026-08-31")),
            "A seeded range that is not adjacent must not stand in for the "
            "previous period; the calendar fallback is exact for Kenya.")
