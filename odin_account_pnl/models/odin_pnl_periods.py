"""Reporting periods: the presets of the date chip, stepping with the arrows,
dividing a range into months or quarters, and comparison periods.

These mirror the Odoo 19 Enterprise date selector, which "enables the
division of periods and navigation between periods"
(https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting/budget.html),
and the presets of ``account.report.default_opening_date_filter`` declared in
Community (addons/account/models/account_report.py), whose default is the
previous month.

Plain functions over ``datetime.date`` so they can be tested on their own.
The fiscal year comes from ``res.company.compute_fiscalyear_dates``.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo.tools import date_utils

PRESETS = (
    "this_month",
    "previous_month",
    "this_quarter",
    "previous_quarter",
    "this_year",
    "previous_year",
    "year_to_date",
    "custom",
)
DEFAULT_PRESET = "previous_month"
DIVISIONS = ("none", "month", "quarter")
COMPARISONS = ("none", "previous_period", "previous_year", "custom")
MAX_COMPARISONS = 12


def preset_range(preset, today, fiscal_year):
    """``(date_from, date_to)`` of a preset seen from ``today``.

    :param fiscal_year: callable returning the fiscal year ``(from, to)``
        containing a date.
    """
    if preset == "this_month":
        return date_utils.get_month(today)
    if preset == "previous_month":
        return date_utils.get_month(today.replace(day=1) - timedelta(days=1))
    if preset == "this_quarter":
        return date_utils.get_quarter(today)
    if preset == "previous_quarter":
        return date_utils.get_quarter(date_utils.get_quarter(today)[0] - timedelta(days=1))
    if preset == "this_year":
        return fiscal_year(today)
    if preset == "previous_year":
        return fiscal_year(fiscal_year(today)[0] - timedelta(days=1))
    if preset == "year_to_date":
        return fiscal_year(today)[0], today
    raise ValueError(preset)


def whole_months(date_from, date_to):
    """Number of whole calendar months the range covers exactly, else 0."""
    if date_from.day != 1 or date_to != date_utils.get_month(date_to)[1]:
        return 0
    return (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1


def shift(date_from, date_to, steps):
    """The range moved ``steps`` times its own length (negative: back).

    A range of whole months moves by months, so stepping back from February
    gives January in full; any other range moves by its number of days.
    """
    months = whole_months(date_from, date_to)
    if months:
        start = date_from + relativedelta(months=months * steps)
        end = date_utils.get_month(start + relativedelta(months=months - 1))[1]
        return start, end
    days = (date_to - date_from).days + 1
    return date_from + timedelta(days=days * steps), date_to + timedelta(days=days * steps)


def divide(date_from, date_to, unit):
    """The calendar months or quarters overlapping the range, clipped to it."""
    getter = date_utils.get_month if unit == "month" else date_utils.get_quarter
    periods = []
    cursor = date_from
    while cursor <= date_to:
        start, end = getter(cursor)
        periods.append((max(start, date_from), min(end, date_to)))
        cursor = end + timedelta(days=1)
    return periods


def comparisons(date_from, date_to, mode, count, custom=None):
    """The comparison ranges, most recent first."""
    if mode == "previous_period":
        return [shift(date_from, date_to, -step) for step in range(1, count + 1)]
    if mode == "previous_year":
        ranges = []
        for step in range(1, count + 1):
            start = date_from - relativedelta(years=step)
            end = date_to - relativedelta(years=step)
            if whole_months(date_from, date_to):
                end = date_utils.get_month(end)[1]
            ranges.append((start, end))
        return ranges
    if mode == "custom" and custom:
        return [custom]
    return []


def kind_of(date_from, date_to, fiscal_year):
    """What the range is, for its label: month, quarter, fiscal year or other."""
    months = whole_months(date_from, date_to)
    if months == 1:
        return "month"
    if months == 3 and date_utils.get_quarter(date_from) == (date_from, date_to):
        return "quarter"
    if (date_from, date_to) == fiscal_year(date_from):
        return "year"
    return "range"
