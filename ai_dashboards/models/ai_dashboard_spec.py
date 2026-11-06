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
WIDGET_TYPES = {"kpi", "bar", "line", "pie", "donut", "table", "pivot",
                "pnl", "aging", "banks"}

# Summary cards the server works out itself, each over the one model that holds
# its answer: profit and loss from journal items, what is owed by due date from
# invoices or bills, and the state of each bank from its journal.
CARD_MODELS = {"pnl": "account.move.line", "aging": "account.move",
               "banks": "account.journal"}

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

# Cards that show where things stand today rather than what happened over a
# period. A page-wide period would change what they mean — an aging card that
# only counted invoices dated this year would hide every older debt, which is
# exactly the debt it exists to show — so date filters pass them by. Every
# other filter still reaches them: "what does this vendor owe" is a fair
# question of an aging card.
POSITION_CARDS = {"aging", "banks"}

# How many records one many2one filter may name at once. A reader picking
# vendors does not pick hundreds; a client sending hundreds is not a reader.
MAX_FILTER_IDS = 100

# The 12-column grid the renderer lays out against.
GRID_COLUMNS = 12

# How many rows and columns a pivot shows at once, per axis. Grouping two real
# dimensions routinely produces thousands of combinations - customers by
# product is 2,000 x 500 - and a grid that large is not a table, it is a
# denial-of-service on the reader and the database at once. Both axes page.
PIVOT_AXIS_CAP = 50

TOP_LEVEL_KEYS = {"schema", "title", "description", "filters", "widgets",
                  "compare"}
WIDGET_KEYS = {"id", "type", "title", "span", "query", "format", "compare",
               "drill", "color", "period", "caption"}
QUERY_KEYS = {"model", "domain", "group_by", "measures", "order", "limit"}
FILTER_KEYS = {"key", "type", "label", "default", "field", "domain"}
FORMAT_KEYS = {"kind", "decimals", "suffix", "negate"}
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
        # An order term must be something _read_group can actually resolve:
        # one of this query's own groupings, or one of its own aggregates in
        # full "field:agg" form. A bare field name looks obviously right and
        # is not — Odoo answers "Aggregate method is mandatory" at render
        # time, long after anyone could connect it to the spec. Catching it
        # here turns an opaque failure into a message the model can act on.
        allowed = set(group_by) | set(measures)
        for chunk in order.split(","):
            parts = chunk.strip().split()
            if not parts:
                _fail(_("Widget '%s': order is empty.") % widget_id)
            if len(parts) > 2 or (len(parts) == 2 and
                                  parts[1].lower() not in ("asc", "desc")):
                _fail(_("Widget '%s': order must be \"<term> asc\" or "
                        "\"<term> desc\".") % widget_id)
            term = parts[0]
            if term not in allowed:
                _fail(_(
                    "Widget '%(w)s': cannot order by '%(term)s'. Order by one "
                    "of this tile's own groupings or measures: %(ok)s. Note a "
                    "measure needs its aggregate — \"amount_total:sum desc\", "
                    "not \"amount_total desc\".",
                    w=widget_id, term=term, ok=", ".join(sorted(allowed))))

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
        if not scope:
            # None means "not gated"; an empty scope means none applies to this
            # user (the default one archived and no other active): refused.
            _fail(_(
                "No AI MCP permission matrix applies to you, so a dashboard "
                "cannot be built. An administrator can set one up under "
                "AI MCP → Permissions."))
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

    # Grouping and aggregating both become SQL, so the field has to be a real
    # column. A computed field that is not stored has no column to group on —
    # Odoo raises "Cannot convert ... to SQL because it is not stored" at
    # render time, long after anything could connect it to the spec. Domains
    # are left alone: a non-stored field can still be searchable.
    for spec_name, role in (
            [(g, _("group_by")) for g in query["group_by"]]
            + [(m, _("measure")) for m in query["measures"] if m != "__count"]):
        root = _field_root(spec_name)
        if not fields_meta.get(root, {}).get("store"):
            _fail(_(
                "Widget '%(w)s': '%(f)s' is calculated on the fly and is not "
                "held in the database, so it cannot be used as a %(role)s. "
                "Pick a stored field — get_schema shows which — or count "
                "records with __count.",
                w=widget_id, f=root, role=role))

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
    if widget_type == "pivot":
        if group_count != 2:
            _fail(_(
                "Widget '%(w)s' is a pivot, so it needs exactly two group_by: "
                "the first becomes the rows, the second the columns. It has "
                "%(n)s.", w=widget_id, n=group_count))
        if len(clean["query"]["measures"]) != 1:
            _fail(_(
                "Widget '%(w)s' is a pivot, so it takes exactly one measure — "
                "that is the number in each cell. It has %(n)s. Use a table if "
                "you need several.", w=widget_id,
                n=len(clean["query"]["measures"])))

    if widget_type in CARD_MODELS:
        if clean["query"]["model"] != CARD_MODELS[widget_type]:
            _fail(_("Widget '%(w)s': a %(t)s card reads %(m)s.", w=widget_id,
                    t=widget_type, m=CARD_MODELS[widget_type]))
        if group_count:
            _fail(_("Widget '%s' works out its own groups, so it must not "
                    "group by anything.") % widget_id)
    if widget.get("period") is not None:
        # The card's own period, which the reader can change on the card.
        if widget["period"] not in PERIODS:
            _fail(_("Widget '%(w)s': period '%(p)s' is not one of: %(ok)s.",
                    w=widget_id, p=widget["period"],
                    ok=", ".join(sorted(PERIODS))))
        if widget_type in ("aging", "banks"):
            _fail(_("Widget '%s' shows today's position, so it has no period.")
                  % widget_id)
        clean["period"] = widget["period"]
    if widget.get("caption"):
        # With it, the card leads with its total, in these words under it.
        clean["caption"] = _text(widget["caption"], _("%s caption") % what)

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
        if fmt.get("negate"):
            # Ledger balances read debit-positive, so profit is the negative of
            # what the lines add up to. Only where one number per point is drawn.
            if widget.get("type") not in ("kpi", "line", "bar"):
                _fail(_("Widget '%s': negate works on a kpi, line or bar.")
                      % widget_id)
            clean_fmt["negate"] = True
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
    if key.startswith("_"):
        # The canvas keeps its own view state next to the filters — the
        # comparison, each card's period — under keys that start with an
        # underscore. A filter named like one would be overwritten by it.
        _fail(_("Filter key '%s' may not start with an underscore; those "
                "names are reserved.") % key)
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

    if flt.get("domain") not in (None, [], "") and filter_type != "many2one":
        _fail(_(
            "Filter '%s': only a many2one filter takes a domain. It narrows "
            "the records the reader can pick from, such as vendors only.")
            % key)

    if filter_type == "date_range":
        default = flt.get("default", "this_year")
        if default not in PERIODS:
            _fail(_(
                "Filter '%(k)s': '%(d)s' is not a period this module knows. "
                "Use one of: %(ok)s.", k=key, d=default,
                ok=", ".join(sorted(PERIODS))))
        clean["default"] = default
        # Which date field the period applies to. Optional: when absent, or
        # when a tile's model has no such field, the renderer uses that
        # model's own usual date field.
        if flt.get("field"):
            clean["field"] = _plain_field(flt["field"], key, what)
        return clean

    if not flt.get("field"):
        _fail(_(
            "Filter '%s' must name the field it filters on, for example "
            "\"partner_id\" for a vendor or customer.") % key)
    clean["field"] = _plain_field(flt["field"], key, what)

    default = flt.get("default")
    if filter_type == "many2one":
        if default not in (None, False, []):
            ids = record_ids(default)
            if not ids or len(ids) != len(
                    default if isinstance(default, (list, tuple)) else [default]):
                _fail(_(
                    "Filter '%s': the default must be a record id, or a list "
                    "of them — whole numbers above zero.") % key)
            clean["default"] = ids
        if flt.get("domain") not in (None, [], ""):
            clean["domain"] = parse_domain(
                flt["domain"], _("Filter '%s' domain") % key)
    elif default not in (None, False, ""):
        # selection
        if not isinstance(default, str):
            _fail(_("Filter '%s': the default must be one of the field's "
                    "values, as text.") % key)
        clean["default"] = default
    return clean


def _plain_field(name, key, what):
    """One field of the tile's own model: no traversal, no aggregate.

    A filter has to land on the same field in every tile it reaches, and say
    which records its picker offers. Both are only well defined for a field
    that sits directly on the model.
    """
    name = _text(name, _("%s field") % what, limit=64)
    if "." in name or ":" in name or name.startswith("_"):
        _fail(_(
            "Filter '%(k)s': '%(f)s' must be a field directly on the tiles' "
            "model, such as \"partner_id\" — not a path through another "
            "record.", k=key, f=name))
    return name


def _validate_filters_against_database(filters, widgets, env, scope):
    """Check each filter lands somewhere real, as the right kind of field.

    A filter that matches no tile is a control that does nothing, and the
    person moving it has no way to know — so it is refused here, where the
    assistant can still fix it, rather than discovered later by a reader.
    Tiles whose model lacks the field are fine: the filter passes them by.
    """
    for flt in filters:
        name = flt.get("field")
        if not name:
            continue  # a date range on each model's own date field
        hits = []
        for widget in widgets:
            model_name = widget["query"]["model"]
            meta = env[model_name].sudo().fields_get(
                [name], attributes=["type", "relation", "selection"])
            if name in meta:
                hits.append((model_name, meta[name]))
        if not hits:
            _fail(_(
                "Filter '%(k)s': none of this dashboard's tiles has a field "
                "named '%(f)s', so it would filter nothing. Call get_schema "
                "on the tiles' models to see their fields.",
                k=flt["key"], f=name))

        wanted = ("date", "datetime") if flt["type"] == "date_range" \
            else (flt["type"],)
        model_name, first = hits[0]
        if first["type"] not in wanted:
            _fail(_(
                "Filter '%(k)s' is a %(t)s filter, but '%(f)s' on %(m)s is a "
                "%(ft)s field.", k=flt["key"], t=flt["type"], f=name,
                m=model_name, ft=first["type"]))

        if scope:
            for model_name, _meta in hits:
                line = scope.line_for_model(model_name)
                if line and name in line.blacklisted_fields():
                    _fail(_(
                        "Filter '%(k)s' uses '%(f)s', which is blocked for "
                        "this connection.", k=flt["key"], f=name))

        if flt["type"] == "many2one" and flt.get("domain"):
            comodel = env[first["relation"]]
            for term in flt["domain"]:
                if isinstance(term, list):
                    root = _field_root(term[0]).split(".", 1)[0]
                    if root not in comodel._fields:
                        _fail(_(
                            "Filter '%(k)s' domain: '%(f)s' does not exist on "
                            "'%(m)s', the model its picker offers.",
                            k=flt["key"], f=root, m=first["relation"]))
        if flt["type"] == "selection" and flt.get("default") is not None:
            values = [value for value, _label in first.get("selection") or []]
            if flt["default"] not in values:
                _fail(_(
                    "Filter '%(k)s': default '%(d)s' is not one of %(f)s's "
                    "values: %(ok)s.", k=flt["key"], d=flt["default"], f=name,
                    ok=", ".join(str(v) for v in values)))


# ------------------------------------------------- filters at render time
# Pure functions, no database: what a filter narrows to and which tiles it
# reaches are decided here, once, and the renderer, the drill-through and the
# emails all ask the same questions of the same code.
def record_ids(raw):
    """Record ids from whatever arrived, and nothing else.

    The canvas sends a list of numbers; an old client, a URL or a hand-written
    spec may send one number or digits as text. Anything that is not a whole
    number above zero is dropped rather than passed to the ORM, where a string
    would quietly be matched against record *names* instead.
    """
    if raw is None or raw is False:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    ids = []
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, str) and item.strip().isdigit():
            item = int(item.strip())
        if isinstance(item, int) and item > 0 and item not in ids:
            ids.append(item)
    return ids[:MAX_FILTER_IDS]


def filter_value(flt, filter_values):
    """What one filter narrows to right now, or None for "everything".

    The reader's choice wins over the spec's default, and choosing "all" is a
    choice: a key that is present but empty means everything, which is not
    the same as a key that is absent and so still on its default. A value
    that cannot be meant — a period nobody offers, an id that is not one —
    falls back to the default for a period and to everything otherwise.
    """
    filter_values = filter_values or {}
    key = flt.get("key")
    chosen = filter_values[key] if key in filter_values else flt.get("default")
    kind = flt.get("type")
    if kind == "date_range":
        if chosen not in PERIODS:
            chosen = flt.get("default")
        return chosen if chosen in PERIODS and chosen != "all_time" else None
    if kind == "many2one":
        return record_ids(chosen) or None
    if kind == "selection":
        return chosen if isinstance(chosen, str) and chosen else None
    return None


def card_period_key(widget_id):
    """Where the canvas keeps the period a reader chose on one card."""
    return "__period:%s" % widget_id


def filters_for_widget(widget, spec):
    """The filters that reach one tile.

    Usually all of them. Two exceptions, both about time: a card with a period
    of its own uses that instead of the page's, and a card showing today's
    position takes no period at all. Every other filter — a vendor, a status —
    still applies, which is the whole point of having one.
    """
    filters = list(spec.get("filters") or [])
    if widget.get("period") or widget.get("type") in POSITION_CARDS:
        filters = [f for f in filters if f.get("type") != "date_range"]
    if widget.get("period"):
        filters.append({"key": card_period_key(widget.get("id")),
                        "type": "date_range", "label": widget.get("title"),
                        "default": widget["period"]})
    return filters


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
    clean = {
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
    if env is not None:
        # After the widgets: a filter is checked against the tiles it would
        # reach, and those have to be known to be real first.
        _validate_filters_against_database(
            clean["filters"], clean["widgets"], env, scope)
    return clean


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

    if spec.get("filters"):
        def filter_phrase(flt):
            label = flt.get("label") or flt.get("key")
            if flt.get("type") == "date_range":
                return _("%(label)s (a period, %(field)s)", label=label,
                         field=flt.get("field") or _("each model's own date"))
            return _("%(label)s (%(field)s)", label=label,
                     field=flt.get("field"))
        lines.append(_(
            "The reader can narrow it by: %s. Each filter applies to every "
            "tile whose model has that field.") % ", ".join(
                filter_phrase(f) for f in spec["filters"]))

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
