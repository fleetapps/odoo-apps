# -*- coding: utf-8 -*-
"""Pivot: two dimensions, paged independently.

The correctness trap this file exists to avoid: ``_read_group``'s ``limit``
counts **groups**, and with two groupings a group is a *combination*. Asking
for ``limit=50`` over ``[partner_id, date_order:month]`` returns fifty
customer-month pairs — perhaps three customers — not fifty rows by twelve
columns. Building a pivot straight from that produces a grid whose axes are
whatever happened to fall inside the first fifty combinations, which is wrong
in a way that looks entirely plausible.

So the axes are resolved separately, then the cells are fetched for exactly the
page being shown. Four queries rather than one, and each is cheap:

1. the row axis, ordered and paged
2. the column axis, ordered and paged
3. one ``count_distinct`` per axis, for an honest "of 2,143"
4. the cells, restricted to the shown rows and columns

Date axes are always ordered chronologically. That is what anyone wants from a
time axis, and it also makes the cell restriction exact: a contiguous window
can be expressed as ``>= first`` and ``< last + one period``, whereas a
measure-ordered date axis would be scattered and a range filter would drag in
periods the page is not showing.
"""
import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from . import ai_dashboard_spec as spec_lib

_logger = logging.getLogger(__name__)

CAP = spec_lib.PIVOT_AXIS_CAP

# Beyond this many distinct values an axis reports "N+" rather than an exact
# total. Counting is a single cheap aggregate, but a truthful ceiling beats
# pretending precision matters at that scale.
COUNT_CEILING = 100000

GRANULARITY_SPANS = {
    "day": relativedelta(days=1),
    "week": relativedelta(weeks=1),
    "month": relativedelta(months=1),
    "quarter": relativedelta(months=3),
    "year": relativedelta(years=1),
}


class AIDashboardPivot(models.AbstractModel):
    _name = "ai.dashboard.pivot"
    _description = "AI Dashboard Pivot Builder"

    @api.model
    def build(self, model, query, domain, offsets=None):
        """The grid for one page of a pivot.

        Runs entirely as the calling user, like every other read in this
        module — a pivot is not a privileged view of anything.
        """
        offsets = offsets or {}
        row_spec, col_spec = query["group_by"][0], query["group_by"][1]
        measure = query["measures"][0]
        row_offset = max(0, int(offsets.get("row") or 0))
        col_offset = max(0, int(offsets.get("col") or 0))

        rows = self._axis(model, domain, row_spec, measure, row_offset,
                          query.get("order"))
        cols = self._axis(model, domain, col_spec, measure, col_offset, None)

        cells, totals = self._cells(model, domain, row_spec, col_spec,
                                    measure, rows, cols)
        return {
            "measure": measure,
            "rows": rows,
            "cols": cols,
            "cells": cells,
            "row_totals": totals["rows"],
            "col_totals": totals["cols"],
            "grand_total": totals["grand"],
        }

    # ------------------------------------------------------------------ axis
    def _axis(self, model, domain, spec, measure, offset, order):
        """One axis: the values on this page, and how many there are in all."""
        root, granularity = self._split(spec)
        # A date axis reads chronologically or not at all — and ordering it by
        # the measure would scatter it, which breaks the contiguous-range
        # restriction the cell query depends on.
        axis_order = spec if granularity else (order or "%s DESC" % measure)

        try:
            groups = model._read_group(
                domain, groupby=[spec], aggregates=[measure],
                offset=offset, limit=CAP + 1, order=axis_order)
        except ValueError:
            # An order the ORM will not resolve should not take the tile down;
            # fall back to the axis's natural order.
            groups = model._read_group(
                domain, groupby=[spec], aggregates=[measure],
                offset=offset, limit=CAP + 1)

        has_more = len(groups) > CAP
        groups = groups[:CAP]

        values = []
        for row in groups:
            raw = row[0]
            values.append({
                "key": self._key(raw),
                "label": self._label(raw, granularity),
                "raw": self._raw(raw),
            })
        return {
            "spec": spec,
            "field": root,
            "granularity": granularity,
            "values": values,
            "offset": offset,
            "has_more": has_more,
            "has_previous": offset > 0,
            "total": self._distinct(model, domain, root),
            "cap": CAP,
        }

    def _distinct(self, model, domain, field):
        """How many distinct values this axis has in total.

        `count_distinct` is one aggregate over the same domain — far cheaper
        than materialising every group just to take its length, which is what
        an honest "of 2,143" would otherwise cost.
        """
        try:
            rows = model._read_group(
                domain, groupby=[], aggregates=["%s:count_distinct" % field])
        except (ValueError, KeyError):
            return None
        if not rows or not rows[0]:
            return 0
        total = rows[0][0] or 0
        return total if total <= COUNT_CEILING else COUNT_CEILING

    # ----------------------------------------------------------------- cells
    def _cells(self, model, domain, row_spec, col_spec, measure, rows, cols):
        """The numbers, for exactly the rows and columns on screen."""
        empty = ({}, {"rows": {}, "cols": {}, "grand": 0})
        if not rows["values"] or not cols["values"]:
            return empty

        scoped = list(domain)
        scoped += self._restrict(rows)
        scoped += self._restrict(cols)

        # At most CAP x CAP combinations can match, so this is bounded by
        # construction; the +1 is the usual truncation tell.
        groups = model._read_group(
            scoped, groupby=[row_spec, col_spec], aggregates=[measure],
            limit=CAP * CAP + 1)

        wanted_rows = {v["key"] for v in rows["values"]}
        wanted_cols = {v["key"] for v in cols["values"]}
        cells = {}
        row_totals, col_totals, grand = {}, {}, 0
        for group in groups:
            rkey = self._key(group[0])
            ckey = self._key(group[1])
            # A range restriction on a date axis can admit a period the page
            # is not showing; drop anything outside the requested set rather
            # than letting it distort a total.
            if rkey not in wanted_rows or ckey not in wanted_cols:
                continue
            value = self._number(group[2])
            cells["%s|%s" % (rkey, ckey)] = value
            row_totals[rkey] = row_totals.get(rkey, 0) + value
            col_totals[ckey] = col_totals.get(ckey, 0) + value
            grand += value
        return cells, {"rows": row_totals, "cols": col_totals, "grand": grand}

    def _restrict(self, axis):
        """Narrow the cell query to the values this page shows."""
        values = axis["values"]
        if not values:
            return []
        if axis["granularity"]:
            # Contiguous because a date axis is ordered chronologically.
            starts = [self._as_date(v["raw"]) for v in values]
            starts = [d for d in starts if d]
            if not starts:
                return []
            first, last = min(starts), max(starts)
            span = GRANULARITY_SPANS.get(axis["granularity"],
                                         relativedelta(months=1))
            return [[axis["field"], ">=", fields.Date.to_string(first)],
                    [axis["field"], "<", fields.Date.to_string(last + span)]]
        raws = [v["raw"] for v in values]
        # `in` does not match NULL, so an "Unassigned" bucket has to be asked
        # for explicitly or its whole row silently empties.
        if any(r is False or r is None for r in raws):
            concrete = [r for r in raws if r is not False and r is not None]
            if not concrete:
                return [[axis["field"], "=", False]]
            return ["|", [axis["field"], "=", False],
                    [axis["field"], "in", concrete]]
        return [[axis["field"], "in", raws]]

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _split(spec):
        if ":" in spec:
            field, granularity = spec.split(":", 1)
            return field, granularity
        return spec, None

    @staticmethod
    def _key(value):
        """A stable, JSON-safe identity for a group value."""
        if isinstance(value, models.BaseModel):
            return "r%s" % (value.id or 0)
        if value is False or value is None:
            return "__none__"
        return str(value)

    def _label(self, value, granularity):
        if isinstance(value, models.BaseModel):
            return value.display_name or _("Unassigned")
        if value is False or value is None:
            return _("Unassigned")
        if granularity:
            date = self._as_date(value)
            if date:
                if granularity == "year":
                    return str(date.year)
                if granularity == "month":
                    return date.strftime("%b %Y")
                if granularity == "quarter":
                    return "Q%s %s" % ((date.month - 1) // 3 + 1, date.year)
                return fields.Date.to_string(date)
        return str(value)

    @staticmethod
    def _raw(value):
        if isinstance(value, models.BaseModel):
            return value.id
        if value is None:
            return False
        if hasattr(value, "isoformat"):
            return fields.Date.to_string(value)
        return value

    @staticmethod
    def _as_date(value):
        if value in (False, None):
            return None
        if hasattr(value, "year") and not isinstance(value, str):
            return value.date() if hasattr(value, "date") else value
        try:
            return fields.Date.to_date(str(value)[:10])
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or value is None:
            return 0
        return value if isinstance(value, (int, float)) else 0
