# -*- coding: utf-8 -*-
"""Which filters fit this dashboard.

Every dashboard wants a period. Past that it depends entirely on what the
tiles are about: a purchasing dashboard wants Vendor and Product, petty cash
wants Payee and Vehicle, a sales board wants Customer and Salesperson, and a
board of accounting summary cards wants no record filter at all. Guessing
that from the dashboard's title is what an assistant would do without help;
this works it out from the tiles' own fields instead, so a suggestion always
lands on something real.

Pure on purpose — no database. The MCP tool gathers the field descriptions
(as the author, within the connection's scope) and hands them over; the same
function then runs in a test, or against a copy of a client's schema, and
gives the same answer.

The vocabulary below is a ranking, not a gate: a comodel nobody listed here —
a custom ``x_vehicle`` — is still offered, below the familiar ones.
"""
import re

from odoo import _

from . import ai_dashboard_spec as spec_lib

# Records worth narrowing a dashboard by, most useful first, with the field
# names that usually mean "the" one on a model: the customer on a sale order
# is partner_id, not partner_shipping_id. Priority is a ranking weight.
COMODELS = {
    "res.partner": (100, ["partner_id", "commercial_partner_id",
                          "order_partner_id"]),
    "product.product": (80, ["product_id"]),
    "product.template": (75, ["product_tmpl_id", "product_template_id"]),
    "res.users": (60, ["user_id", "invoice_user_id"]),
    "hr.employee": (60, ["employee_id"]),
    "project.project": (60, ["project_id"]),
    "fleet.vehicle": (60, ["vehicle_id"]),
    "account.journal": (55, ["journal_id"]),
    "account.analytic.account": (55, ["analytic_account_id", "account_id"]),
    "crm.team": (55, ["team_id"]),
    "pos.config": (50, ["config_id"]),
    "account.account": (50, ["account_id"]),
    "product.category": (50, ["categ_id", "product_category_id"]),
    "stock.warehouse": (50, ["warehouse_id"]),
    "hr.department": (45, ["department_id"]),
    "res.company": (40, ["company_id"]),
    "stock.location": (35, ["location_id", "location_dest_id"]),
    "stock.picking.type": (35, ["picking_type_id"]),
    "res.country": (30, ["country_id"]),
    "res.country.state": (25, ["state_id"]),
    "utm.campaign": (25, ["campaign_id"]),
    "utm.source": (20, ["source_id"]),
    "utm.medium": (20, ["medium_id"]),
}
# Anything else that is not plumbing still counts, just below these.
DEFAULT_PRIORITY = 20

# Comodels where each field is its own dimension rather than one concept
# under several names. Analytic plans are the case that matters: a journal
# item's "Project" and "Vehicles" both point at analytic accounts, and are
# two different questions — so each becomes its own filter, named for its
# plan, instead of competing to be "the" analytic account.
PER_FIELD_COMODELS = {"account.analytic.account": 55}
# Where the same analytic accounts sit on models that spread an amount across
# several (analytic.mixin in Odoo 17+): journal items, bills, expenses. A plan
# filter reaches those through this field as well.
ANALYTIC_SPREAD = "distribution_analytic_account_ids"
PLAN_COLUMN = re.compile(r"^x_plan(\d+)_id$")

# Plumbing: never a filter a person reaches for.
TECHNICAL_COMODEL = re.compile(
    r"^(ir\.|mail\.|bus\.|base\.|res\.currency|res\.lang|res\.groups|uom\.|"
    r"resource\.|report\.|digest\.|iap\.|auth_|portal\.|rating\.|web\.|"
    r"account\.(full|partial)\.reconcile|account\.tax|account\.payment\.term|"
    r"account\.fiscal\.position|account\.incoterms|account\.cash\.rounding|"
    r"product\.packaging|product\.pricelist|stock\.(lot|quant|package|route|"
    r"rule|warehouse\.orderpoint)|account\.move$|account\.move\.line$|"
    r"sale\.order$|sale\.order\.line$|sale\.order\.template$|"
    r"purchase\.order$|purchase\.order\.line$|account\.payment$|"
    r"account\.payment\.(method|method\.line|order)$|"
    r"account\.reconcile\.model$|account\.tax\.(group|repartition\.line)$|"
    r"account\.bank\.statement|res\.partner\.bank$|followup\.|"
    r"online\.bank\.statement\.provider$|account\.asset\.(category|profile)$|"
    r"contract\.line$|hr\.expense$|credit\.control\.policy$)")
TECHNICAL_FIELDS = {
    "create_uid", "write_uid", "message_main_attachment_id",
    "activity_user_id", "activity_type_id", "activity_calendar_event_id",
    "currency_id", "company_currency_id", "partner_shipping_id",
    "partner_invoice_id", "partner_bank_id", "fiscal_position_id",
    "website_id", "rating_last_value", "sequence",
}

# Selection fields worth a dropdown, ranked. Anything else with a handful of
# values is offered below these.
SELECTIONS = {
    "payment_state": 45, "state": 40, "invoice_status": 35,
    "move_type": 30, "stage": 30, "priority": 20, "type": 15,
}
TECHNICAL_SELECTIONS = {
    "activity_state", "activity_exception_decoration", "kanban_state",
    "color", "display_type", "tax_calculation_rounding_method",
    "auto_post", "edi_state", "access_url", "reference_type",
    "invoice_reference_type", "invoice_reference_model",
}
# Past the ones named above, only fields that read as a status or a kind of
# record: settings that happen to be selections ("Reconcile Mode", "Payment
# QR-code") are not something anyone narrows a dashboard by.
STATUS_LIKE = re.compile(r"(^|_)(state|status|stage|priority|type)$")
MAX_SELECTION_VALUES = 12

# A filter that reaches only a sliver of a large board is noise beside the
# ones that reach most of it — unless it is one of the dimensions people
# always reach for (a product, a salesperson, a project).
MIN_COVERAGE = 0.25
ALWAYS_WORTH_IT = 75
# What the author already slices a tile by — payees, vehicles, accounts — is
# the clearest sign of what its readers will want to narrow by.
GROUPED_BONUS = 40


PURCHASE_MODELS = re.compile(r"^purchase\.")
SALE_MODELS = re.compile(r"^(sale\.|pos\.order)")


def _domain_values(domain, field, operators=("=", "in")):
    """Every value a tile's own domain holds ``field`` to, if any.

    Only equality counts by default: ``x_plan2_id != False`` says "has a
    vehicle", which is exactly when a Vehicle filter is useful, not that the
    tile is already narrowed to one.
    """
    pinned = []
    for term in domain or []:
        if isinstance(term, list) and len(term) == 3 and term[0] == field \
                and term[1] in operators:
            value = term[2]
            pinned.extend(value if isinstance(value, (list, tuple)) else [value])
    return pinned


def _constrained(domain, field):
    """Whether a tile's domain already decides which values of ``field`` it
    shows — any comparison at all, including "not cancelled"."""
    return any(isinstance(term, list) and len(term) == 3 and term[0] == field
               for term in domain or [])


def _partner_role(tiles):
    """Vendor, Customer or neither, from what the tiles read.

    Purchase models are about vendors and sales models about customers.
    Journal entries say so through their move_type or account type; a cash
    journal of mixed payees says nothing, and gets the neutral word.
    """
    roles = set()
    for tile in tiles:
        model, domain = tile["model"], tile["domain"]
        if PURCHASE_MODELS.match(model):
            roles.add("vendor")
            continue
        if SALE_MODELS.match(model):
            roles.add("customer")
            continue
        kinds = [str(v) for v in _domain_values(domain, "move_type")
                 + _domain_values(domain, "move_id.move_type")
                 + _domain_values(domain, "account_id.account_type")
                 + _domain_values(domain, "account_type")]
        if kinds and all(k.startswith("in_") or k == "liability_payable"
                         for k in kinds):
            roles.add("vendor")
        elif kinds and all(k.startswith("out_") or k == "asset_receivable"
                           for k in kinds):
            roles.add("customer")
        else:
            roles.add("other")
    return roles.pop() if len(roles) == 1 else "other"


def _slug(label, taken):
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "filter"
    if key[0].isdigit():
        key = "f_" + key
    candidate, n = key, 2
    while candidate in taken:
        candidate, n = "%s_%s" % (key, n), n + 1
    taken.add(candidate)
    return candidate


def _majority(names):
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return max(counts, key=lambda n: (counts[n], -names.index(n)))


def _tiles(spec):
    tiles = []
    for widget in spec.get("widgets") or []:
        query = widget.get("query") or {}
        tiles.append({
            "id": widget.get("id"),
            "model": query.get("model"),
            "domain": query.get("domain") or [],
            "type": widget.get("type"),
            "own_period": bool(widget.get("period")),
            "grouped": {spec_lib._field_root(g) for g in query.get("group_by") or []},
            "position": widget.get("type") in spec_lib.POSITION_CARDS,
        })
    return tiles


def suggest(spec, metadata, model_labels=None, companies=1, distinct=None,
            comodel_fields=None, analytic_plans=None, limit=6):
    """Filters that would fit this dashboard, best first.

    ``metadata`` maps each tile model to its ``fields_get`` description
    (type, relation, selection, string, searchable), already stripped of
    anything the connection may not see. ``distinct`` optionally maps
    ``(model, field)`` to how many different values the tile's records have,
    so a filter that could only ever show one value sinks. ``comodel_fields``
    maps a comodel to its field names, to know whether "vendors only" can be
    expressed there. ``analytic_plans`` maps each analytic plan's column
    (``account_id``, ``x_plan2_id``) to ``(plan id, plan name)``, so a plan
    filter is named for its plan and offers only its accounts. Returns the
    suggestions, a description of each, and notes on what was left out and
    why.
    """
    analytic_plans = analytic_plans or {}
    model_labels = model_labels or {}
    distinct = distinct or {}
    comodel_fields = comodel_fields or {}
    tiles = [t for t in _tiles(spec) if t["model"] in metadata]
    existing = spec.get("filters") or []
    taken = {f.get("key") for f in existing}
    notes, out = [], []
    if not tiles:
        return {"suggestions": [], "notes": [_(
            "None of this dashboard's tiles could be read, so there is "
            "nothing to suggest from.")]}

    def coverage(reached):
        return len(reached) / float(len(tiles))

    # ------------------------------------------------------------- period
    dated = [t for t in tiles if not t["own_period"] and not t["position"]]
    if any(f.get("type") == "date_range" for f in existing):
        pass
    elif not dated:
        notes.append(_(
            "No Period filter: every tile has its own period or shows "
            "today's position, so a page-wide period would change nothing."))
    else:
        per_tile = {}
        for tile in dated:
            fields = metadata[tile["model"]]
            # Exactly the renderer's choice (`_date_field`): the first
            # searchable date in the shared order.
            per_tile[tile["id"]] = next(
                (name for name in spec_lib.DATE_FIELD_ORDER
                 if fields.get(name, {}).get("type") in ("date", "datetime")
                 and fields[name].get("searchable", True)), None)
        reached = [tid for tid, name in per_tile.items() if name]
        if reached:
            names = [per_tile[tid] for tid in reached]
            flt = {"key": _slug(_("period"), taken), "type": "date_range",
                   "label": _("Period"), "default": "this_year"}
            if len(set(names)) == 1:
                flt["field"] = names[0]
            out.append({
                "filter": flt, "score": 1000,
                "reaches": reached,
                "passes_by": [t["id"] for t in tiles if t["id"] not in reached],
                "why": _("Windows each tile on its date: %s.") % ", ".join(
                    "%s → %s" % (tid, per_tile[tid]) for tid in reached),
            })

    # ---------------------------------------------------------- many2one
    already = {(f.get("type"), f.get("field")) for f in existing}
    by_comodel = {}
    for tile in tiles:
        fields = metadata[tile["model"]]
        for name, meta in fields.items():
            relation = meta.get("relation")
            if meta.get("type") != "many2one" or not relation \
                    or name in TECHNICAL_FIELDS or name.startswith("_") \
                    or not meta.get("searchable", True) \
                    or TECHNICAL_COMODEL.match(relation) \
                    or relation == tile["model"]:
                continue
            if relation == "res.company" and companies < 2:
                continue
            group = (relation, name if relation in PER_FIELD_COMODELS else None)
            by_comodel.setdefault(group, {}).setdefault(
                tile["id"], []).append(name)

    for (relation, _own), tile_fields in by_comodel.items():
        priority, usual = COMODELS.get(relation, (DEFAULT_PRIORITY, []))
        if relation in PER_FIELD_COMODELS:
            priority, usual = PER_FIELD_COMODELS[relation], []
        chosen, ambiguous = {}, []
        # The name most tiles use wins on a tile with several candidates.
        common = [n for names in tile_fields.values() for n in names]
        for tile_id, names in tile_fields.items():
            if len(names) == 1:
                chosen[tile_id] = names[0]
                continue
            pick = next((n for n in usual if n in names), None)
            if not pick:
                shared = [n for n in names if common.count(n) > 1]
                pick = shared[0] if len(shared) == 1 else None
            if not pick:
                guess = relation.split(".")[-1] + "_id"
                pick = guess if guess in names else None
            if pick:
                chosen[tile_id] = pick
            else:
                ambiguous.append("%s (%s)" % (tile_id, ", ".join(sorted(names))))
        if relation in PER_FIELD_COMODELS:
            # The same plan on models that spread across several accounts.
            for tile in tiles:
                spread = metadata[tile["model"]].get(ANALYTIC_SPREAD) or {}
                if tile["id"] not in chosen and spread.get("relation") == relation \
                        and spread.get("searchable", True):
                    chosen[tile["id"]] = ANALYTIC_SPREAD
        if not chosen:
            if ambiguous:
                notes.append(_(
                    "%(model)s: more than one field on %(tiles)s could mean "
                    "it, so none was assumed. Name one with `fields` if you "
                    "want this filter.", model=relation,
                    tiles="; ".join(ambiguous)))
            continue

        tile_model = {t["id"]: t["model"] for t in tiles}
        main = _majority(list(chosen.values()))
        if relation in PER_FIELD_COMODELS and _own:
            main = _own  # the plan column names the filter, not the spread
        if ("many2one", main) in already:
            continue
        # A tile whose own domain already pins this field to one record gains
        # nothing from the filter — it could only agree or empty the tile — so
        # only the others count towards offering it. It still *reaches* the
        # pinned ones (the renderer applies it wherever the field is), and the
        # report says so.
        tile_by_id = {t["id"]: t for t in tiles}
        reaches = list(chosen)
        reached = [tid for tid, name in chosen.items()
                   if len(_domain_values(tile_by_id[tid]["domain"], name)) != 1]
        grouped = any(chosen[tid] in tile_by_id[tid]["grouped"] for tid in reached)
        if not reached or (coverage(reached) < MIN_COVERAGE and not grouped
                           and priority < ALWAYS_WORTH_IT):
            continue
        if grouped:
            priority += GROUPED_BONUS

        values = [distinct.get((tile_model[tid], chosen[tid])) for tid in reached]
        known = [v for v in values if v is not None]
        variety = max(known) if known else None
        if variety is not None and variety < 2:
            notes.append(_(
                "%(label)s (%(field)s) left out: the tiles' records have "
                "%(n)s value(s) for it today.", label=model_labels.get(
                    relation, relation), n=variety, field=main))
            continue

        reached_tiles = [t for t in tiles if t["id"] in reached]
        label, picker = None, None
        if relation == "res.partner":
            role = _partner_role(reached_tiles)
            partner_fields = comodel_fields.get("res.partner") or ()
            if role == "vendor":
                label = _("Vendor")
                if "supplier_rank" in partner_fields:
                    picker = [["supplier_rank", ">", 0]]
            elif role == "customer":
                label = _("Customer")
                if "customer_rank" in partner_fields:
                    picker = [["customer_rank", ">", 0]]
            else:
                label = _("Partner")
        if not label:
            # The field's own name on the tiles that use the main spelling —
            # "Vehicles", "Buyer", "Journal" — which is what the reader sees on
            # those records' forms; the model's name only when there is none.
            label = next((metadata[tile_model[tid]][chosen[tid]].get("string")
                          for tid in reached if chosen[tid] == main), None)
            label = label or model_labels.get(relation) or relation

        if relation in PER_FIELD_COMODELS:
            plan_id, plan_name = analytic_plans.get(main, (None, None))
            match = PLAN_COLUMN.match(main)
            plan_id = plan_id or (int(match.group(1)) if match else None)
            label = plan_name or label
            if plan_id and "root_plan_id" in (comodel_fields.get(relation) or ()):
                # Only this plan's accounts in the picker: vehicles, not
                # projects — the column's own domain does the same on a form.
                picker = [["root_plan_id", "=", plan_id]]
        flt = {"key": _slug(label, taken), "type": "many2one",
               "label": label, "field": main}
        exceptions = {tile_model[tid]: chosen[tid] for tid in reaches
                      if chosen[tid] != main}
        if exceptions:
            flt["fields"] = exceptions
        if picker:
            flt["domain"] = picker
        why = _("%(n)s of %(total)s tiles have it (%(fields)s).",
                n=len(reached), total=len(tiles),
                fields=", ".join(sorted({"%s.%s" % (tile_model[t], chosen[t])
                                         for t in reached})))
        if variety:
            why += " " + _("Up to %s different values in the data.") % variety
        if ambiguous:
            why += " " + _("Not assumed on: %s.") % "; ".join(ambiguous)
        out.append({
            "filter": flt,
            "score": priority * (0.5 + 0.5 * coverage(reached)),
            "reaches": reaches,
            "passes_by": [t["id"] for t in tiles if t["id"] not in reaches],
            "why": why,
        })

    # --------------------------------------------------------- selection
    by_field = {}
    for tile in tiles:
        for name, meta in metadata[tile["model"]].items():
            options = meta.get("selection") or []
            if meta.get("type") != "selection" or name in TECHNICAL_SELECTIONS \
                    or name.startswith("_") or not meta.get("searchable", True) \
                    or not 2 <= len(options) <= MAX_SELECTION_VALUES \
                    or not (name in SELECTIONS or STATUS_LIKE.search(name)):
                continue
            by_field.setdefault(name, []).append(
                (tile, frozenset(value for value, _label in options), meta))
    for name, entries in by_field.items():
        if ("selection", name) in already:
            continue
        # The renderer builds the dropdown from the first tile with the field
        # and lands it only where the values are the same set (see
        # `_filter_meta`): "posted" is nothing to a sale order. Same rule
        # here, so what is suggested is what will happen.
        reference = entries[0][1]
        members = [(tile, meta) for tile, values, meta in entries
                   if values == reference]
        reaches = [tile["id"] for tile, _meta in members]
        # A tile that already decides which statuses it shows gains nothing.
        useful = [tile for tile, _meta in members
                  if not _constrained(tile["domain"], name)]
        if not useful:
            continue
        grouped = any(name in tile["grouped"] for tile in useful)
        if coverage(useful) < MIN_COVERAGE and not grouped:
            continue
        known = [distinct.get((tile["model"], name)) for tile in useful]
        known = [v for v in known if v is not None]
        if known and max(known) < 2:
            continue
        label = members[0][1].get("string") or name
        out.append({
            "filter": {"key": _slug(label, taken), "type": "selection",
                       "label": label, "field": name},
            "score": (SELECTIONS.get(name, 10) + (GROUPED_BONUS if grouped else 0))
            * (0.5 + 0.5 * coverage(useful)),
            "reaches": reaches,
            "passes_by": [t["id"] for t in tiles if t["id"] not in reaches],
            "why": _("%(n)s of %(total)s tiles share these values: %(v)s.",
                     n=len(reaches), total=len(tiles),
                     v=", ".join(sorted(str(v) for v in reference))),
        })

    out.sort(key=lambda s: -s["score"])
    for suggestion in out:
        suggestion.pop("score")
    return {"suggestions": out[:limit], "notes": notes}
