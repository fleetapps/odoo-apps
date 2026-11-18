# -*- coding: utf-8 -*-
"""Words for the values a dashboard groups by, and the date buckets behind them.

``_read_group`` hands back what the database holds: ``"sale"`` for a sale
order's state, a ``datetime`` for "March", ``True`` for a checkbox. Shown as
they are, a donut's legend reads ``cancel / draft / sale`` and a chart's axis
reads ``2026-03-01`` — accurate, and exactly what made the dashboards look like
a database dump. Everything here turns those into the words the rest of Odoo
already uses, in the reader's language:

* a selection value becomes its label, via the public ``fields_get``;
* a date bucket becomes ``Mar 2026``, through ``odoo.tools.misc.format_date``
  (LDML patterns, the reader's locale), with a short form for an axis whose
  points all fall in one year;
* a checkbox becomes Yes / No.

The same functions also lay out a date axis bucket by bucket, so a chart over
"this year" shows January to now rather than only the months that happened to
have records. Week buckets start where the ORM starts them — on the reader's
language's first day of the week (``res.lang.week_start``), not on a Monday
assumed here — or the filled weeks would not line up with the read ones.
"""
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, fields, models
from odoo.tools.misc import format_date, get_lang

# (long, short) LDML patterns per granularity. The long form is for tables,
# tooltips and emails; the short one for an axis whose points share a year.
DATE_PATTERNS = {
    "day": ("d MMM yyyy", "d MMM"),
    "month": ("MMM yyyy", "MMM"),
    "quarter": ("QQQ yyyy", "QQQ"),
    "year": ("yyyy", "yyyy"),
}
GRANULARITIES = ("day", "week", "month", "quarter", "year")
# A filled axis never grows past this, whatever window it was asked for: a
# daily axis over "all time" would otherwise be thousands of empty points.
MAX_BUCKETS = 400


def split_spec(spec):
    """``"invoice_date:month"`` -> ``("invoice_date", "month")``."""
    if isinstance(spec, str) and ":" in spec:
        field, granularity = spec.split(":", 1)
        return field, granularity
    return spec, None


def as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return fields.Date.to_date(value[:10])
        except (ValueError, TypeError):
            return None
    return None


def bucket_start(env, day, granularity):
    """The first day of the bucket ``day`` falls in, as ``_read_group`` cuts it."""
    if granularity == "day":
        return day
    if granularity == "week":
        # res.lang.week_start is "1" (Monday) to "7" (Sunday), the same scale
        # as isoweekday(), which is what the ORM truncates weeks against.
        first = int(get_lang(env).week_start)
        return day - timedelta(days=(day.isoweekday() - first) % 7)
    if granularity == "month":
        return day.replace(day=1)
    if granularity == "quarter":
        return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)
    if granularity == "year":
        return date(day.year, 1, 1)
    return day


def bucket_step(granularity):
    return {
        "day": relativedelta(days=1),
        "week": relativedelta(weeks=1),
        "month": relativedelta(months=1),
        "quarter": relativedelta(months=3),
        "year": relativedelta(years=1),
    }[granularity]


def buckets(env, start, end, granularity):
    """Every bucket from the one holding ``start`` up to ``end`` (exclusive)."""
    out = []
    current = bucket_start(env, start, granularity)
    step = bucket_step(granularity)
    while current < end and len(out) < MAX_BUCKETS:
        out.append(current)
        current = current + step
    return out


def date_labels(env, value, granularity):
    """``(long, short)`` words for one date bucket."""
    day = as_date(value)
    if not day:
        return _("Unassigned"), _("Unassigned")
    if granularity == "week":
        return (_("Week of %s", format_date(env, day, date_format="d MMM yyyy")),
                format_date(env, day, date_format="d MMM"))
    long_fmt, short_fmt = DATE_PATTERNS.get(granularity, DATE_PATTERNS["day"])
    return (format_date(env, day, date_format=long_fmt),
            format_date(env, day, date_format=short_fmt))


class Labeler:
    """Turns one grouping's raw values into words, for one model.

    Built once per tile and grouping, so a selection's labels are looked up
    once rather than once per row.
    """

    def __init__(self, env, model, spec):
        self.env = env
        self.fname, self.granularity = split_spec(spec)
        self.field = model._fields.get(self.fname)
        self.selection = {}
        if self.field is not None and self.field.type == "selection":
            # fields_get, not field.selection: it is the public API, it
            # resolves callables, and it answers in the reader's language.
            described = model.fields_get([self.fname], ["selection"])
            self.selection = dict(
                (described.get(self.fname) or {}).get("selection") or [])

    def labels(self, value):
        """``(long, short)`` for one raw group value."""
        if isinstance(value, models.BaseModel):
            name = value.display_name or _("Unassigned")
            return name, name
        if self.field is not None and self.field.type == "boolean":
            word = _("Yes") if value else _("No")
            return word, word
        if value is False or value is None:
            word = _("Unassigned")
            return word, word
        if self.granularity or isinstance(value, (date, datetime)):
            return date_labels(self.env, value, self.granularity or "day")
        if self.selection:
            word = self.selection.get(value, str(value))
            return word, word
        return str(value), str(value)

    def label(self, value):
        return self.labels(value)[0]


def column_title(model, spec):
    """A table column's heading, for a grouping or a measure, in words.

    ``partner_id`` becomes "Customer" and ``amount_untaxed_signed:sum`` becomes
    "Untaxed Amount Signed" — whatever Odoo itself calls the field, in the
    reader's language — and ``__count`` becomes "Count".
    """
    if spec == "__count":
        return _("Count")
    fname, suffix = split_spec(spec)
    described = model.fields_get([fname], ["string"]).get(fname) or {}
    title = described.get("string") or fname
    if suffix == "avg":
        return _("Average %s", title)
    if suffix == "min":
        return _("Lowest %s", title)
    if suffix == "max":
        return _("Highest %s", title)
    if suffix == "count_distinct":
        return _("Distinct %s", title)
    return title


def range_label(env, start, end, compact=True):
    """A window in words; ``end`` is exclusive, as every window here is.

    Compact, a whole calendar unit is named for what it is — "Oct 2026",
    "Q3 2026", "2026" — which is how "compared with" reads best. Otherwise,
    and for anything that is not a whole unit, the dates themselves:
    "1 – 30 Nov 2026", "28 Oct – 3 Nov 2026".
    """
    start, end = as_date(start), as_date(end)
    if not start or not end or end <= start:
        return ""
    if compact and start.day == 1:
        if end == start + relativedelta(months=1):
            return format_date(env, start, date_format="MMM yyyy")
        if start.month in (1, 4, 7, 10) and end == start + relativedelta(months=3):
            return format_date(env, start, date_format="QQQ yyyy")
        if start.month == 1 and end == start + relativedelta(years=1):
            return format_date(env, start, date_format="yyyy")
    last = end - timedelta(days=1)
    if start == last:
        return format_date(env, start, date_format="d MMM yyyy")
    if start.year == last.year:
        head = str(start.day) if start.month == last.month \
            else format_date(env, start, date_format="d MMM")
        return "%s – %s" % (head, format_date(env, last, date_format="d MMM yyyy"))
    return "%s – %s" % (format_date(env, start, date_format="d MMM yyyy"),
                        format_date(env, last, date_format="d MMM yyyy"))


def midnight(day, as_datetime):
    """A filled bucket's group value, in the form ``_read_group`` returns it."""
    return datetime.combine(day, time.min) if as_datetime else day
