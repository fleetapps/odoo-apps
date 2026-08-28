# -*- coding: utf-8 -*-
"""Dashboard specification: schema, validation, and plain-English description.

This module is the security boundary of the product. Everything an AI produces
passes through ``validate()`` before it is stored, and nothing else in the
codebase is permitted to trust a spec that has not been through here.

The governing rule is that a spec is **data, never instructions**. There is no
eval, no template compilation and no SQL anywhere in this file or downstream of
it. A widget names a model, a domain, a grouping and a measure; the renderer
turns that into an ORM ``_read_group`` call. Nothing in a spec can express
"run this" - only "read that".

Validation is strict rather than permissive on purpose. An unknown key is
rejected, not ignored: silently dropping a key an assistant thought it was
setting produces a dashboard that is subtly not the one anybody asked for, and
the person reading it has no way to tell. A loud refusal is fixable, because
the model gets the message and corrects the spec on its next turn - which is
the whole point of a tool-calling loop.
"""
import ast

from odoo import _
from odoo.exceptions import ValidationError

SCHEMA_ID = "ai-dashboards/1"

# The v1 widget vocabulary. Deliberately small and deliberately frozen: every
# addition is a schema change plus a renderer branch plus a documentation
# update, and a vocabulary that grows on request ends up with six ways to draw
# a bar chart.
WIDGET_TYPES = {"kpi", "bar", "line", "pie", "donut", "table"}

# Widgets that plot one grouping against one measure, so exactly one group_by
# is meaningful. `table` and `kpi` are the exceptions, handled below.
SINGLE_GROUP_TYPES = {"bar", "line", "pie", "donut"}

AGGREGATES = {"sum", "avg", "min", "max", "count"}
DATE_GRANULARITIES = {"day", "week", "month", "quarter", "year"}

# Relative periods a filter may name. Resolved to real dates at render time, in
# the viewer's timezone, so a saved dashboard means the same thing in June that
# it meant in January.
PERIODS = {
    "today", "this_week", "this_month", "this_quarter", "this_year",
    "last_week", "last_month", "last_quarter", "last_year",
    "last_7_days", "last_30_days", "last_90_days", "all_time",
}

FILTER_TYPES = {"date_range", "selection", "many2one"}

# The 12-column grid the renderer lays out against.
GRID_COLUMNS = 12

TOP_LEVEL_KEYS = {"schema", "title", "description", "filters", "widgets",
                  "compare"}
WIDGET_KEYS = {"id", "type", "title", "span", "query", "format", "compare",
               "drill", "color"}
QUERY_KEYS = {"model", "domain", "group_by", "measures", "order", "limit"}
FILTER_KEYS = {"key", "type", "label", "default", "field"}
FORMAT_KEYS = {"kind", "decimals", "suffix"}
COMPARE_KEYS = {"to"}

FORMAT_KINDS = {"plain", "monetary", "percent", "integer", "duration"}
# "none" is a real value, not an absence: a dashboard-wide comparison has to
# be switchable off for one tile without deleting the key, and a widget whose
# comparison is meaningless (a pie of two periods) should be able to say so.
COMPARE_TARGETS = {"none", "previous_period", "previous_year"}

# Only these can carry a comparison. A pie or donut of two periods is not a
# chart anybody can read, and a table's rows do not align across windows, so
# offering it there would produce something confidently wrong.
COMPARABLE_TYPES = {"kpi", "bar", "line"}

# Domain operators we accept. Anything outside this list is refused rather than
# passed to the ORM - not because the ORM would break, but because an operator
# nobody reviewed is an operator nobody can reason about.
DOMAIN_OPERATORS = {
    "=", "!=", ">", ">=", "<", "<=", "=?", "=like", "like", "not like",
    "ilike", "not ilike", "=ilike", "in", "not in", "child_of", "parent_of",
    "any", "not any",
}
DOMAIN_JOINERS = {"&", "|", "!"}

MAX_WIDGETS = 24
MAX_DOMAIN_TERMS = 40
MAX_TITLE = 120


class SpecError(ValidationError):
    """A spec was refused. The message is written to be read by a model.

    Phrased so the assistant that produced the spec can act on it directly:
    which widget, which key, and what would have been acceptable instead.
    """


def _fail(message):
    raise SpecError(message)


def _require_dict(value, what):
    if not isinstance(value, dict):
        _fail(_("%s must be an object.") % what)
    return value


def _reject_unknown(payload, allowed, what):
    """Strict-mode key checking, with the permitted set named in the error.

    Naming the alternatives matters: a model told only that a key is invalid
    will usually try a synonym, whereas one shown the whole set corrects on the
    first retry.
    """
    unknown = set(payload) - allowed
    if unknown:
        _fail(_(
            "%(what)s has unsupported key(s): %(bad)s. Allowed here: %(ok)s.",
            what=what, bad=", ".join(sorted(unknown)),
            ok=", ".join(sorted(allowed))))
    for key in payload:
        if not isinstance(key, str) or key.startswith("_"):
            _fail(_("%(what)s: '%(key)s' is not a permitted key name.",
                    what=what, key=key))


def _text(value, what, limit=MAX_TITLE, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        _fail(_("%s must be a non-empty string.") % what)
    value = value.strip()
    if len(value) > limit:
        _fail(_("%(what)s is longer than %(limit)s characters.",
                what=what, limit=limit))
    return value


def _field_root(name):
    """The bare field name from `date:month` or `amount:sum`."""
    return name.split(":", 1)[0] if isinstance(name, str) else name


# --------------------------------------------------------------------- domain
def parse_domain(raw, what="domain"):
    """Turn a domain into a list, accepting only literals.

    A domain arrives either already-structured (from JSON) or as a string, and
    the string form is parsed with ``ast.literal_eval`` - never ``eval`` and
    never ``safe_eval``. literal_eval will not call a function, resolve a name
    or import anything; it evaluates literals and nothing else. That is the
    property being relied on here.
    """
    if raw in (None, "", [], ()):
        return []
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError, TypeError, MemoryError,
                RecursionError):
            _fail(_(
                "%s must be a list of triples such as "
                "[[\"state\", \"=\", \"sale\"]]. It could not be read as one.")
                % what)
    if isinstance(raw, tuple):
        raw = list(raw)
    if not isinstance(raw, list):
        _fail(_("%s must be a list.") % what)
    if len(raw) > MAX_DOMAIN_TERMS:
        _fail(_("%(what)s has more than %(max)s terms.",
                what=what, max=MAX_DOMAIN_TERMS))

    out = []
    for term in raw:
        if isinstance(term, str):
            if term not in DOMAIN_JOINERS:
                _fail(_(
                    "%(what)s contains '%(term)s'. A bare string in a domain "
                    "may only be one of: %(ok)s.",
                    what=what, term=term, ok=", ".join(sorted(DOMAIN_JOINERS))))
            out.append(term)
            continue
        if isinstance(term, (list, tuple)):
            if len(term) != 3:
                _fail(_("%s: each condition needs exactly three parts "
                        "(field, operator, value).") % what)
            field, operator, value = term
            if not isinstance(field, str) or not field or field.startswith("_"):
                _fail(_("%(what)s: '%(f)s' is not a valid field name.",
                        what=what, f=field))
            if operator not in DOMAIN_OPERATORS:
                _fail(_(
                    "%(what)s: operator '%(op)s' is not supported. Use one of: "
                    "%(ok)s.", what=what, op=operator,
                    ok=", ".join(sorted(DOMAIN_OPERATORS))))
            _check_value(value, what)
            out.append([field, operator, value])
            continue
        _fail(_("%s: a domain term must be a condition or an operator string.")
              % what)
    return out


def _check_value(value, what, depth=0):
    """A domain value may be a scalar or a flat-ish list of scalars."""
    if depth > 2:
        _fail(_("%s: nested values are too deep.") % what)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _check_value(item, what, depth + 1)
        return
    _fail(_("%s: values must be text, numbers, booleans or lists of those.")
          % what)


# ---------------------------------------------------------------------- query
def _validate_query(query, widget_id, env=None, scope=None):
    _require_dict(query, _("Widget '%s' query") % widget_id)
    _reject_unknown(query, QUERY_KEYS, _("Widget '%s' query") % widget_id)

    model_name = query.get("model")
    if not isinstance(model_name, str) or not model_name:
        _fail(_("Widget '%s' must name a model.") % widget_id)

    group_by = query.get("group_by") or []
    if isinstance(group_by, str):
        group_by = [group_by]
    if not isinstance(group_by, list):
        _fail(_("Widget '%s': group_by must be a list.") % widget_id)
    for entry in group_by:
        if not isinstance(entry, str) or not entry or entry.startswith("_"):
            _fail(_("Widget '%(w)s': '%(g)s' is not a valid group_by.",
                    w=widget_id, g=entry))
        if ":" in entry:
            granularity = entry.split(":", 1)[1]
            if granularity not in DATE_GRANULARITIES:
                _fail(_(
                    "Widget '%(w)s': '%(g)s' is not a date granularity. Use "
                    "one of: %(ok)s.", w=widget_id, g=granularity,
                    ok=", ".join(sorted(DATE_GRANULARITIES))))

    measures = query.get("measures") or ["__count"]
    if isinstance(measures, str):
        measures = [measures]
    if not isinstance(measures, list) or not measures:
        _fail(_("Widget '%s': measures must be a non-empty list.") % widget_id)
    for measure in measures:
        if measure == "__count":
            continue
        if not isinstance(measure, str) or ":" not in measure:
            _fail(_(
                "Widget '%(w)s': measure '%(m)s' must be either \"__count\" or "
                "\"field:aggregate\", for example \"amount_total:sum\".",
                w=widget_id, m=measure))
        field, _sep, aggregate = measure.partition(":")
        if not field or field.startswith("_"):
            _fail(_("Widget '%(w)s': '%(f)s' is not a valid measure field.",
                    w=widget_id, f=field))
        if aggregate not in AGGREGATES:
            _fail(_(
                "Widget '%(w)s': '%(a)s' is not a supported aggregate. Use one "
                "of: %(ok)s.", w=widget_id, a=aggregate,
                ok=", ".join(sorted(AGGREGATES))))

    order = query.get("order")
    if order is not None:
        order = _text(order, _("Widget '%s' order") % widget_id, limit=200)
        for chunk in order.split(","):
            parts = chunk.strip().split()
            if not parts or parts[0].startswith("_"):
                _fail(_("Widget '%s': order names an invalid field.") % widget_id)
            if len(parts) > 2 or (len(parts) == 2 and
                                  parts[1].lower() not in ("asc", "desc")):
                _fail(_("Widget '%s': order must be \"field asc\" or "
                        "\"field desc\".") % widget_id)

    limit = query.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            _fail(_("Widget '%s': limit must be a positive whole number.")
                  % widget_id)

    clean = {
        "model": model_name,
        "domain": parse_domain(query.get("domain"),
                               _("Widget '%s' domain") % widget_id),
        "group_by": group_by,
        "measures": measures,
    }
    if order is not None:
        clean["order"] = order
    if limit is not None:
        clean["limit"] = limit

    if env is not None:
        _validate_against_database(clean, widget_id, env, scope)
    return clean


def _validate_against_database(query, widget_id, env, scope):
    """Check the model and fields really exist, and are in scope.

    Two different gates, and the distinction is worth keeping straight:

    * the **governance scope** decides what an assistant may build over. It is
      the same matrix that governs every other MCP tool, so a dashboard can
      never reach data the connection was not already allowed to read;
    * the **viewer's own access rights** decide what any given person sees when
      they open the dashboard, and those are applied at render time rather than
      here, because the person who opens it is usually not the person who
      built it.

    Only the first belongs in validation. Checking the author's record rules
    here would bake one person's visibility into a shared artifact.
    """
    model_name = query["model"]
    if model_name not in env:
        _fail(_("There is no model named '%s' in this database.") % model_name)

    blacklist = set()
    if scope is not None:
        line = scope.line_for_model(model_name)
        if not line or not line.can_read:
            _fail(_(
                "'%(model)s' is not readable in the '%(scope)s' permission "
                "matrix, so a dashboard cannot be built over it. An "
                "administrator can enable it under AI MCP → Permissions → "
                "Model Permissions.", model=model_name, scope=scope.name))
        blacklist = line.blacklisted_fields()

    model = env[model_name].sudo()
    fields_meta = model.fields_get(attributes=["type", "string", "store"])

    referenced = (
        [(_field_root(g), _("group_by")) for g in query["group_by"]]
        + [(_field_root(m), _("measure")) for m in query["measures"]
           if m != "__count"]
        + [(_field_root(t[0]), _("filter")) for t in query["domain"]
           if isinstance(t, list)]
    )
    for field_name, role in referenced:
        # A domain may legitimately traverse relations (`partner_id.country_id`);
        # only the first hop is checkable here, which is the one that matters
        # for the blacklist.
        root = field_name.split(".", 1)[0]
        if root in blacklist:
            _fail(_(
                "Widget '%(w)s' uses '%(f)s', which is blocked for this "
                "connection.", w=widget_id, f=root))
        if root not in fields_meta:
            _fail(_(
                "Widget '%(w)s': %(role)s '%(f)s' does not exist on "
                "'%(model)s'. Call get_schema to see the real field names.",
                w=widget_id, role=role, f=root, model=model_name))

    for measure in query["measures"]:
        if measure == "__count":
            continue
        root = _field_root(measure)
        if fields_meta.get(root, {}).get("type") not in (
                "integer", "float", "monetary"):
            _fail(_(
                "Widget '%(w)s': '%(f)s' is not a numeric field, so it cannot "
                "be totalled. Use __count, or pick a numeric field.",
                w=widget_id, f=root))


# --------------------------------------------------------------------- widget
def _validate_widget(widget, index, seen_ids, env=None, scope=None):
    what = _("Widget %s") % (index + 1)
    _require_dict(widget, what)
    _reject_unknown(widget, WIDGET_KEYS, what)

    widget_id = widget.get("id") or "w%s" % (index + 1)
    widget_id = _text(widget_id, _("%s id") % what, limit=64)
    if not widget_id.replace("_", "").replace("-", "").isalnum():
        _fail(_("Widget id '%s' may only contain letters, numbers, hyphens "
                "and underscores.") % widget_id)
    if widget_id in seen_ids:
        _fail(_("Widget id '%s' is used more than once.") % widget_id)
    seen_ids.add(widget_id)

    widget_type = widget.get("type")
    if widget_type not in WIDGET_TYPES:
        _fail(_(
            "Widget '%(w)s': type '%(t)s' is not supported. Use one of: "
            "%(ok)s.", w=widget_id, t=widget_type,
            ok=", ".join(sorted(WIDGET_TYPES))))

    span = widget.get("span", 6)
    if not isinstance(span, int) or isinstance(span, bool) \
            or not 1 <= span <= GRID_COLUMNS:
        _fail(_("Widget '%(w)s': span must be a whole number from 1 to %(n)s.",
                w=widget_id, n=GRID_COLUMNS))

    clean = {
        "id": widget_id,
        "type": widget_type,
        "title": _text(widget.get("title"), _("%s title") % what),
        "span": span,
        "query": _validate_query(widget.get("query") or {}, widget_id,
                                 env, scope),
        "drill": bool(widget.get("drill", True)),
    }

    group_count = len(clean["query"]["group_by"])
    if widget_type == "kpi" and group_count:
        _fail(_("Widget '%s' is a KPI, so it must not group by anything - it "
                "shows a single number.") % widget_id)
    if widget_type in SINGLE_GROUP_TYPES and group_count != 1:
        _fail(_(
            "Widget '%(w)s' is a %(t)s chart, so it needs exactly one "
            "group_by. It has %(n)s.",
            w=widget_id, t=widget_type, n=group_count))
    if widget_type == "table" and not group_count:
        _fail(_("Widget '%s' is a table, so it needs at least one group_by.")
              % widget_id)

    if "color" in widget:
        color = widget["color"]
        if not isinstance(color, int) or isinstance(color, bool) \
                or not 0 <= color <= 11:
            _fail(_("Widget '%s': color must be a whole number from 0 to 11.")
                  % widget_id)
        clean["color"] = color

    if "format" in widget and widget["format"] is not None:
        fmt = _require_dict(widget["format"], _("Widget '%s' format") % widget_id)
        _reject_unknown(fmt, FORMAT_KEYS, _("Widget '%s' format") % widget_id)
        kind = fmt.get("kind", "plain")
        if kind not in FORMAT_KINDS:
            _fail(_(
                "Widget '%(w)s': format kind '%(k)s' is not supported. Use one "
                "of: %(ok)s.", w=widget_id, k=kind,
                ok=", ".join(sorted(FORMAT_KINDS))))
        clean_fmt = {"kind": kind}
        decimals = fmt.get("decimals")
        if decimals is not None:
            if not isinstance(decimals, int) or isinstance(decimals, bool) \
                    or not 0 <= decimals <= 6:
                _fail(_("Widget '%s': decimals must be between 0 and 6.")
                      % widget_id)
            clean_fmt["decimals"] = decimals
        if fmt.get("suffix"):
            clean_fmt["suffix"] = _text(fmt["suffix"], _("format suffix"),
                                        limit=16)
        clean["format"] = clean_fmt

    if "compare" in widget and widget["compare"] is not None:
        cmp_ = _validate_compare(widget["compare"],
                                 _("Widget '%s' compare") % widget_id)
        if cmp_["to"] != "none" and widget_type not in COMPARABLE_TYPES:
            _fail(_(
                "Widget '%(w)s' is a %(t)s, which cannot show a comparison. "
                "Only %(ok)s can — a pie of two periods is unreadable, and a "
                "table's rows do not line up across windows.",
                w=widget_id, t=widget_type,
                ok=", ".join(sorted(COMPARABLE_TYPES))))
        clean["compare"] = cmp_

    return clean


def _validate_compare(payload, what):
    cmp_ = _require_dict(payload, what)
    _reject_unknown(cmp_, COMPARE_KEYS, what)
    target = cmp_.get("to")
    if target not in COMPARE_TARGETS:
        _fail(_("%(what)s: `to` must be one of: %(ok)s.",
                what=what, ok=", ".join(sorted(COMPARE_TARGETS))))
    return {"to": target}


# --------------------------------------------------------------------- filter
def _validate_filter(flt, index, seen_keys):
    what = _("Filter %s") % (index + 1)
    _require_dict(flt, what)
    _reject_unknown(flt, FILTER_KEYS, what)

    key = _text(flt.get("key"), _("%s key") % what, limit=64)
    if not key.replace("_", "").isalnum():
        _fail(_("Filter key '%s' may only contain letters, numbers and "
                "underscores.") % key)
    if key in seen_keys:
        _fail(_("Filter key '%s' is used more than once.") % key)
    seen_keys.add(key)

    filter_type = flt.get("type")
    if filter_type not in FILTER_TYPES:
        _fail(_(
            "Filter '%(k)s': type '%(t)s' is not supported. Use one of: "
            "%(ok)s.", k=key, t=filter_type,
            ok=", ".join(sorted(FILTER_TYPES))))

    clean = {
        "key": key,
        "type": filter_type,
        "label": _text(flt.get("label") or key, _("%s label") % what),
    }

    if filter_type == "date_range":
        default = flt.get("default", "this_year")
        if default not in PERIODS:
            _fail(_(
                "Filter '%(k)s': '%(d)s' is not a period this module knows. "
                "Use one of: %(ok)s.", k=key, d=default,
                ok=", ".join(sorted(PERIODS))))
        clean["default"] = default
        # Which date field the period applies to. Optional: when absent the
        # renderer falls back to the model's own default date field.
        if flt.get("field"):
            clean["field"] = _text(flt["field"], _("%s field") % what, limit=64)
    else:
        if not flt.get("field"):
            _fail(_("Filter '%s' must name the field it filters on.") % key)
        clean["field"] = _text(flt["field"], _("%s field") % what, limit=64)
        if flt.get("default") is not None:
            _check_value(flt["default"], _("%s default") % what)
            clean["default"] = flt["default"]

    return clean


# ----------------------------------------------------------------- public API
def validate(spec, env=None, scope=None):
    """Validate a spec and return a normalised copy. Raises SpecError.

    ``env`` and ``scope`` are optional so the shape of a spec can be checked
    without a database - which the tests rely on, and which keeps the pure
    structural rules honest. Pass both in production: without them the model
    and field names are never checked against anything real.
    """
    _require_dict(spec, _("A dashboard specification"))
    _reject_unknown(spec, TOP_LEVEL_KEYS, _("The specification"))

    schema = spec.get("schema")
    if schema != SCHEMA_ID:
        _fail(_(
            "This module understands specifications marked \"%(want)s\". This "
            "one says \"%(got)s\". Call get_dashboard_schema for the current "
            "format.", want=SCHEMA_ID, got=schema or _("nothing")))

    widgets = spec.get("widgets")
    if not isinstance(widgets, list) or not widgets:
        _fail(_("A dashboard needs at least one widget."))
    if len(widgets) > MAX_WIDGETS:
        _fail(_("A dashboard may have at most %(max)s widgets; this one has "
                "%(n)s.", max=MAX_WIDGETS, n=len(widgets)))

    filters = spec.get("filters") or []
    if not isinstance(filters, list):
        _fail(_("filters must be a list."))

    seen_keys = set()
    seen_ids = set()
    compare = None
    if spec.get("compare") is not None:
        compare = _validate_compare(spec["compare"], _("The comparison"))
    return {
        "schema": SCHEMA_ID,
        "compare": compare or {"to": "none"},
        "title": _text(spec.get("title"), _("The dashboard title")),
        "description": _text(spec.get("description"), _("The description"),
                             limit=500, required=False),
        "filters": [_validate_filter(f, i, seen_keys)
                    for i, f in enumerate(filters)],
        "widgets": [_validate_widget(w, i, seen_ids, env, scope)
                    for i, w in enumerate(widgets)],
    }


def describe(spec, env=None):
    """Render a validated spec as plain English.

    This is the feature nobody generating SQL can offer, and it is the thing
    that gets a dashboard trusted: an administrator can read exactly what it
    reads without reading any code. Kept next to the schema so it cannot drift
    from the vocabulary it describes.
    """
    lines = []
    if spec.get("description"):
        lines.append(spec["description"])

    models_used = []
    for widget in spec.get("widgets", []):
        model_name = widget["query"]["model"]
        if model_name not in models_used:
            models_used.append(model_name)

    def label_for(model_name):
        if env is not None and model_name in env:
            name = env["ir.model"]._get(model_name).name
            if name:
                return "%s (%s)" % (name, model_name)
        return model_name

    if models_used:
        lines.append(_("Reads: %s.") % ", ".join(
            label_for(m) for m in models_used))

    for widget in spec.get("widgets", []):
        query = widget["query"]
        measures = ", ".join(
            _("a count of records") if m == "__count"
            else _("%(agg)s of %(field)s", agg=m.split(":", 1)[1],
                   field=m.split(":", 1)[0])
            for m in query["measures"])
        if query["group_by"]:
            grouped = _(", grouped by %s") % ", ".join(
                g.replace(":", " by ") for g in query["group_by"])
        else:
            grouped = ""
        filtered = _(" where %s") % _describe_domain(query["domain"]) \
            if query["domain"] else ""
        lines.append(_(
            "· %(title)s — %(type)s showing %(measures)s from %(model)s"
            "%(grouped)s%(filtered)s.",
            title=widget["title"], type=widget["type"], measures=measures,
            model=query["model"], grouped=grouped, filtered=filtered))

    lines.append(_(
        "Every figure is calculated when you open this dashboard, using your "
        "own Odoo permissions — so two people may correctly see different "
        "numbers, and nothing here is stored."))
    return "\n".join(str(line) for line in lines)


def _describe_domain(domain):
    parts = []
    for term in domain:
        if isinstance(term, str):
            continue  # joiners add noise without adding meaning here
        field, operator, value = term
        parts.append("%s %s %s" % (field, operator, value))
    return _(" and ").join(parts) if parts else ""
