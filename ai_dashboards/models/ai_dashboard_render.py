# -*- coding: utf-8 -*-
"""Turning a specification into figures.

The single most important line in this file is that nothing here runs with
elevated rights. A dashboard is opened by a person, and every query runs as
**that** person through the ordinary ORM, so their ``ir.model.access``, record
rules, field permissions and company access all apply. Two colleagues opening
the same shared dashboard correctly see different numbers, and neither of them
sees anything they could not have found by hand.

That is also why a dashboard is never stale and never leaks: it stores the
question, and the question is asked afresh, by whoever is asking.
"""
import logging
import time
from datetime import date, datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import ai_dashboard_spec as spec_lib

_logger = logging.getLogger(__name__)

# A hard ceiling on rows any single widget may pull back, independent of what
# the spec asks for. A chart with 5,000 categories is not a chart.
WIDGET_ROW_CAP = 500


class AIDashboardRender(models.AbstractModel):
    _name = "ai.dashboard.render"
    _description = "AI Dashboard Renderer"

    # ------------------------------------------------------------ public API
    @api.model
    def render(self, dashboard_id, filter_values=None, offsets=None):
        """Everything the dashboard canvas needs, in one round trip.

        ``offsets`` carries per-widget paging, keyed by widget id — currently
        only pivots use it, which page each axis independently.
        """
        dashboard = self.env["ai.dashboard"].browse(int(dashboard_id))
        dashboard.check_access("read")
        spec = dashboard.spec()
        started = time.time()

        filter_values = filter_values or {}
        # View-time choice beats the spec's default, exactly like the period
        # filter: "show me everything against last year" is a question somebody
        # asks of a dashboard, not a property of it.
        compare_mode = filter_values.get("__compare") or \
            (spec.get("compare") or {}).get("to") or "none"

        widgets = []
        for widget in spec.get("widgets", []):
            widgets.append(self._render_widget(
                widget, spec, filter_values, compare_mode,
                (offsets or {}).get(widget.get("id")) or {}))

        elapsed = int((time.time() - started) * 1000)
        # sudo for the timing only: it is telemetry about the dashboard, not
        # about the person, and a reader with no write access still generates it.
        dashboard.sudo().last_render_ms = elapsed

        return {
            "id": dashboard.id,
            "name": dashboard.name,
            "state": dashboard.state,
            "description": dashboard.description or "",
            "explanation": dashboard.explanation,
            "is_owner": dashboard.owner_id.id == self.env.uid,
            "filters": spec.get("filters", []),
            "compare": compare_mode,
            "widgets": widgets,
            "took_ms": elapsed,
            "currency": self._currency(),
        }

    @api.model
    def sample(self, spec, limit=3):
        """Render a spec that has not been saved yet.

        Used by ``preview_dashboard`` so an assistant can check its own numbers
        look sane before it asks a person to come and look at them.
        """
        rows = []
        for widget in spec.get("widgets", [])[:limit]:
            rendered = self._render_widget(widget, spec, {}, "none")
            rows.append({
                "title": rendered["title"],
                "type": rendered["type"],
                "error": rendered.get("error"),
                "series": rendered.get("series", [])[:5],
                "value": rendered.get("value"),
            })
        return rows

    # ------------------------------------------------------------- internals
    def _render_widget(self, widget, spec, filter_values, compare_mode="none",
                       offsets=None):
        """One widget. An error here degrades this tile, never the page."""
        # A widget may override the dashboard's comparison, including turning
        # it off for itself.
        own = (widget.get("compare") or {}).get("to")
        mode = own if own else compare_mode
        if widget.get("type") not in spec_lib.COMPARABLE_TYPES:
            mode = "none"
        base = {
            "id": widget.get("id"),
            "type": widget.get("type"),
            "title": widget.get("title"),
            "span": widget.get("span", 6),
            "color": widget.get("color"),
            "format": widget.get("format") or {"kind": "plain"},
            "drill": widget.get("drill", True),
            # The editor needs the tile's shape to know what it may safely
            # become: a single grouping can be drawn as any chart or a table,
            # several groupings only as a table, and none at all only as a KPI.
            # Offering a switch that would need the query rewritten is how an
            # editor produces a spec the validator then refuses.
            "group_count": len((widget.get("query") or {}).get("group_by") or []),
            "comparable": widget.get("type") in spec_lib.COMPARABLE_TYPES,
        }
        query = widget.get("query") or {}
        model_name = query.get("model")

        if model_name not in self.env:
            return dict(base, error=_(
                "This tile reads '%s', which is not installed in this "
                "database.") % model_name)

        model = self.env[model_name]
        if not model.has_access("read"):
            # Honest about *why* it is empty. "No data" would be a lie: there
            # may be plenty of data, just none of it theirs.
            return dict(base, error=_(
                "You do not have access to %s, so this tile cannot be shown. "
                "Everything on a dashboard runs with your own permissions.")
                % self._model_label(model_name))

        if widget.get("type") == "pivot":
            try:
                domain = self._effective_domain(query, spec, filter_values)
                grid = self.env["ai.dashboard.pivot"].build(
                    model.with_context(**self._company_context()),
                    query, domain, offsets)
            except (AccessError, UserError) as exc:
                return dict(base, error=str(exc))
            except Exception as exc:  # noqa: BLE001 - one tile, not the page
                _logger.exception("AI Dashboards: pivot %s failed",
                                  widget.get("id"))
                return dict(base, error=_(
                    "This pivot could not be calculated (%s).")
                    % type(exc).__name__)
            out = dict(base, pivot=grid, domain=domain, model=model_name)
            if not grid["rows"]["values"]:
                out["empty"] = _(
                    "No %s records match this tile's filters — for you. "
                    "Someone with wider access may see more.")\
                    % self._model_label(model_name)
            return out

        try:
            domain = self._effective_domain(query, spec, filter_values)
            rows = self._read(model, query, domain)
            # The comparison is read from the *effective* domain, which is the
            # bug this rewrite fixes: the old code looked at the widget's raw
            # domain, so a dashboard whose period comes from a filter - which
            # is every dashboard the schema example produces - silently never
            # showed a trend at all.
            previous = None
            if mode != "none":
                shifted = self._shift_window(domain, mode)
                if shifted is not None:
                    previous = self._read(model, query, shifted)
        except (AccessError, UserError) as exc:
            return dict(base, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - one bad tile, not a dead page
            _logger.exception("AI Dashboards: widget %s failed",
                              widget.get("id"))
            return dict(base, error=_("This tile could not be calculated (%s).")
                        % type(exc).__name__)

        shaped = self._shape(widget, query, rows, previous)
        if mode != "none":
            shaped["compare_mode"] = mode
            if previous is None:
                # Say why rather than quietly showing nothing: without a date
                # window there is no "previous" to compute.
                shaped["compare_note"] = _(
                    "No date range on this tile, so there is nothing to "
                    "compare it against.")
        if not shaped.get("series") and shaped.get("value") is None:
            shaped["empty"] = _("No %s records match this tile's filters — for "
                                "you. Someone with wider access may see more.") \
                % self._model_label(model_name)
        return dict(base, **shaped, domain=domain, model=model_name)

    def _read(self, model, query, domain):
        group_by = list(query.get("group_by") or [])
        measures = list(query.get("measures") or ["__count"])
        aggregates = ["__count" if m == "__count" else m for m in measures]
        limit = min(int(query.get("limit") or WIDGET_ROW_CAP), WIDGET_ROW_CAP)

        rows = model.with_context(**self._company_context())._read_group(
            domain, groupby=group_by, aggregates=aggregates,
            order=query.get("order"), limit=limit + 1)
        # Same truncation discipline the MCP engine uses: never hand back a
        # full page that reads as a complete answer.
        has_more = len(rows) > limit
        return {"rows": rows[:limit], "has_more": has_more,
                "group_by": group_by, "aggregates": aggregates}

    def _shape(self, widget, query, read, previous=None):
        """Turn ORM tuples into something Chart.js can draw."""
        rows, group_by = read["rows"], read["group_by"]
        aggregates = read["aggregates"]
        out = {"has_more": read["has_more"]}

        if widget.get("type") == "kpi":
            value = 0
            if rows:
                # No grouping, so one row whose only entry is the aggregate.
                value = self._number(rows[0][0]) if rows[0] else 0
            out["value"] = value
            if previous is not None:
                prior = previous["rows"]
                out["compare"] = (self._number(prior[0][0])
                                  if prior and prior[0] else 0)
            return out

        series = []
        for row in rows:
            label = self._label(row[0]) if group_by else _("Total")
            value = self._number(row[len(group_by)]) if len(row) > len(group_by) else 0
            series.append({
                "label": label,
                "value": value,
                # Carried so a click can filter the drill-down to this slice.
                "raw": self._raw(row[0]) if group_by else None,
            })
        out["series"] = series
        if previous is not None and group_by:
            # Aligned by position rather than by label: grouping by month puts
            # "2025-08" against "2026-08", which share no label but are the
            # points a reader wants side by side. Labelled as the comparison in
            # the legend so nobody mistakes one for the other.
            prior = previous["rows"]
            out["compare_series"] = [
                self._number(row[len(group_by)])
                if len(row) > len(group_by) else 0
                for row in prior
            ]
        if widget.get("type") == "table":
            out["columns"] = group_by + aggregates
            out["rows"] = [
                [self._label(cell) for cell in row[:len(group_by)]]
                + [self._number(cell) for cell in row[len(group_by):]]
                for row in rows
            ]
        return out

    def _shift_window(self, domain, mode):
        """The same domain, moved back one period or one year.

        Returns None when there is no date window to shift - a real answer
        rather than a failure. "Compared to what?" has no answer for a tile
        with no date range, and saying so beats showing a silent zero.

        Only the *trailing* plain conditions are rewritten, and only when the
        whole window sits inside them. A domain may contain prefix operators
        ('&', '|', '!') that take a fixed number of operands, so removing a
        condition from the middle of one would leave an operator short and
        produce a domain the ORM cannot parse. Everything `_effective_domain`
        appends lands at the end, unprefixed, which is exactly the case this
        handles; anything more tangled declines to compare rather than guess.
        """
        window = self._domain_window(domain)
        if not window:
            return None
        field, start, end = window

        # The run of plain conditions at the end, which carry no operator.
        tail_from = len(domain)
        while tail_from and isinstance(domain[tail_from - 1], (list, tuple)) \
                and len(domain[tail_from - 1]) == 3:
            tail_from -= 1
        head, tail = list(domain[:tail_from]), list(domain[tail_from:])

        bounds = [t for t in tail
                  if t[0] == field and t[1] in (">=", ">", "<", "<=")]
        if len(bounds) != 2:
            # Exactly two, because the rewrite removes what it finds and adds
            # back two: that keeps the number of leaf conditions identical, so
            # any prefix operator earlier in the domain still has the operand
            # count it expects. Three bounds on one field would change that
            # count and could leave an operator short, so decline instead.
            return None

        if mode == "previous_year":
            start -= relativedelta(years=1)
            end -= relativedelta(years=1)
        else:
            span = end - start
            start, end = start - span, start

        kept = [t for t in tail
                if not (t[0] == field and t[1] in (">=", ">", "<", "<="))]
        return head + kept + [
            [field, ">=", fields.Date.to_string(start)],
            [field, "<", fields.Date.to_string(end)],
        ]

    # -------------------------------------------------------------- filters
    def _effective_domain(self, query, spec, filter_values):
        """The widget's own domain, narrowed by whatever the viewer selected."""
        domain = list(query.get("domain") or [])
        for flt in spec.get("filters", []):
            key = flt.get("key")
            chosen = filter_values.get(key, flt.get("default"))
            if chosen in (None, "", "all_time"):
                continue
            if flt.get("type") == "date_range":
                field = flt.get("field") or self._date_field(query["model"])
                if not field:
                    continue
                window = self._period_bounds(chosen)
                if window:
                    start, end = window
                    domain += [[field, ">=", fields.Date.to_string(start)],
                               [field, "<", fields.Date.to_string(end)]]
            elif flt.get("field"):
                domain += [[flt["field"], "=", chosen]]
        return domain

    @api.model
    def _period_bounds(self, period):
        """Resolve a named period to real dates, in the viewer's timezone.

        Stored as a name rather than dates on purpose: "this year" has to still
        mean this year in January, and a dashboard that silently kept last
        year's window would be wrong in the least visible way possible.
        """
        today = fields.Date.context_today(self)
        if period == "today":
            return today, today + timedelta(days=1)
        if period == "this_week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=7)
        if period == "last_week":
            start = today - timedelta(days=today.weekday() + 7)
            return start, start + timedelta(days=7)
        if period == "this_month":
            start = today.replace(day=1)
            return start, start + relativedelta(months=1)
        if period == "last_month":
            start = today.replace(day=1) - relativedelta(months=1)
            return start, start + relativedelta(months=1)
        if period == "this_quarter":
            start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
            return start, start + relativedelta(months=3)
        if period == "last_quarter":
            start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1) \
                - relativedelta(months=3)
            return start, start + relativedelta(months=3)
        if period == "this_year":
            start = date(today.year, 1, 1)
            return start, start + relativedelta(years=1)
        if period == "last_year":
            start = date(today.year - 1, 1, 1)
            return start, start + relativedelta(years=1)
        for days, name in ((7, "last_7_days"), (30, "last_30_days"),
                           (90, "last_90_days")):
            if period == name:
                return today - timedelta(days=days), today + timedelta(days=1)
        return None

    def _domain_window(self, domain):
        """Find the date window a domain already expresses, for comparisons."""
        starts = {}
        ends = {}
        for term in domain:
            if not isinstance(term, list) or len(term) != 3:
                continue
            field, operator, value = term
            parsed = self._as_date(value)
            if parsed is None:
                continue
            if operator in (">=", ">"):
                starts[field] = parsed
            elif operator in ("<", "<="):
                ends[field] = parsed
        for field in starts:
            if field in ends:
                return field, starts[field], ends[field]
        return None

    @staticmethod
    def _as_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return fields.Date.to_date(value[:10])
            except (ValueError, TypeError):
                return None
        return None

    def _date_field(self, model_name):
        """The field a date filter applies to when the spec does not say.

        Ordered by how likely each is to be the one a business means.
        """
        model = self.env[model_name]
        for candidate in ("date", "date_order", "invoice_date", "date_deadline",
                          "date_start", "scheduled_date", "create_date"):
            field = model._fields.get(candidate)
            if field and field.store and field.type in ("date", "datetime"):
                return candidate
        return None

    # --------------------------------------------------------------- helpers
    def _company_context(self):
        return {"allowed_company_ids": self.env.companies.ids
                or self.env.company.ids}

    def _currency(self):
        currency = self.env.company.currency_id
        return {"symbol": currency.symbol, "position": currency.position,
                "decimals": currency.decimal_places}

    def _model_label(self, model_name):
        record = self.env["ir.model"]._get(model_name)
        return record.name or model_name

    @staticmethod
    def _number(value):
        if isinstance(value, bool) or value is None:
            return 0
        if isinstance(value, (int, float)):
            return value
        return 0

    def _label(self, value):
        if isinstance(value, models.BaseModel):
            return value.display_name or _("Unassigned")
        if value in (False, None):
            return _("Unassigned")
        if isinstance(value, (date, datetime)):
            return fields.Date.to_string(value)
        return str(value)

    @staticmethod
    def _raw(value):
        """The value a drill-down should filter on, rather than its label."""
        if isinstance(value, models.BaseModel):
            return value.id
        if isinstance(value, (date, datetime)):
            return fields.Date.to_string(value)
        if isinstance(value, bool) or value is None:
            return False
        return value

    # ----------------------------------------------------------- drill-through
    @api.model
    def drill(self, dashboard_id, widget_id, raw_value=None,
              filter_values=None):
        """Open the records behind a chart segment in a normal Odoo list.

        The feature that makes a dashboard trusted rather than admired: a
        number you cannot get behind is a number you have to take on faith.
        Built entirely from the spec - no AI involved, and the list obeys the
        viewer's permissions like any other list in Odoo.
        """
        dashboard = self.env["ai.dashboard"].browse(int(dashboard_id))
        dashboard.check_access("read")
        spec = dashboard.spec()
        widget = next((w for w in spec.get("widgets", [])
                       if w.get("id") == widget_id), None)
        if not widget:
            raise UserError(_("That tile is no longer part of this dashboard."))
        if not widget.get("drill", True):
            raise UserError(_("This tile does not open its records."))

        query = widget["query"]
        domain = self._effective_domain(query, spec, filter_values or {})
        group_by = query.get("group_by") or []
        if raw_value is not None and group_by:
            field = spec_lib._field_root(group_by[0])
            granularity = (group_by[0].split(":", 1)[1]
                           if ":" in group_by[0] else None)
            domain += self._segment_domain(field, granularity, raw_value)

        return {
            "type": "ir.actions.act_window",
            "name": _("%(widget)s — %(dashboard)s",
                      widget=widget["title"], dashboard=dashboard.name),
            "res_model": query["model"],
            "view_mode": "list,form",
            "domain": domain,
            "context": {"create": False},
            "target": "current",
        }

    def _segment_domain(self, field, granularity, raw_value):
        """Narrow to the clicked slice of the first grouping."""
        if granularity:
            start = self._as_date(raw_value)
            if start is None:
                return []
            spans = {"day": relativedelta(days=1), "week": relativedelta(weeks=1),
                     "month": relativedelta(months=1),
                     "quarter": relativedelta(months=3),
                     "year": relativedelta(years=1)}
            end = start + spans.get(granularity, relativedelta(months=1))
            return [[field, ">=", fields.Date.to_string(start)],
                    [field, "<", fields.Date.to_string(end)]]
        if raw_value in (False, None):
            return [[field, "=", False]]
        return [[field, "=", raw_value]]
