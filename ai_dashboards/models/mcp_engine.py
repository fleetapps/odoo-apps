# -*- coding: utf-8 -*-
"""Dashboard verbs for the MCP engine.

Extends ``mcp.engine`` rather than reimplementing it, so these tools inherit
the whole governance stack for free: capability gating, the per-model
permission matrix, the OAuth scope check, rate limiting and one attributable
audit row per call. A dashboard tool is not a side door - it goes through the
same gates as ``search_records``.

The important asymmetry, stated once here because it runs through everything:
**authorship is gated by the MCP scope, viewing is gated by the viewer.** An
assistant may only build over models the connection can already read; when
somebody later opens the dashboard, their own access rights decide what the
figures contain. See ai_dashboard_render.py.
"""
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.mcp_governance_suite.models.mcp_url import public_base_url

from . import ai_dashboard_spec as spec_lib

_logger = logging.getLogger(__name__)

# Verbs added by this module that change data. Registered through
# mcp.tool._write_handlers() below - a writing handler left unregistered is
# advertised to read-only connections, which is the one way a downstream
# module can quietly punch through the governance layer.
DASHBOARD_WRITE_HANDLERS = {
    "preview_dashboard", "save_dashboard", "delete_dashboard",
}
DASHBOARD_READ_HANDLERS = {
    "get_dashboard_schema", "list_dashboards", "get_dashboard",
    "seed_from_view",
}


class MCPEngine(models.AbstractModel):
    _inherit = "mcp.engine"

    @api.model
    def _model_aware_handlers(self):
        """`seed_from_view` benefits from knowing which models are in scope."""
        return super()._model_aware_handlers() | {"seed_from_view"}

    # ----------------------------------------------------------- discovery
    def _handler_get_dashboard_schema(self, scope, args):
        """Teach the model the format, rather than hoping it guesses.

        By far the most important tool here. A model that has read this builds
        a valid spec first time; one that has not spends three turns being
        corrected by the validator. Returned as data rather than prose so it
        stays in step with the validator automatically.
        """
        return {
            "schema": spec_lib.SCHEMA_ID,
            "how_it_works": _(
                "You describe a dashboard; Odoo draws it. You never write "
                "code, SQL or markup — only this specification. Each widget "
                "names a model, an optional domain, groupings and measures. "
                "Odoo runs those as an ORM read_group when somebody opens the "
                "dashboard, using that person's own permissions, so a saved "
                "dashboard holds the question and never the numbers."),
            "widget_types": {
                "kpi": _("One number. Must not group by anything."),
                "bar": _("Exactly one group_by."),
                "line": _("Exactly one group_by, usually a date granularity."),
                "pie": _("Exactly one group_by. Best under about eight slices."),
                "donut": _("As pie."),
                "table": _("One or more group_by. Shows the rows."),
                "pivot": _(
                    "Exactly two group_by and exactly one measure. The first "
                    "grouping becomes the rows, the second the columns, and "
                    "the measure fills each cell. Both axes show %s at a time "
                    "and the reader pages through the rest, so it is safe over "
                    "high-cardinality fields like customers or products."
                ) % spec_lib.PIVOT_AXIS_CAP,
            },
            "grid": {
                "columns": spec_lib.GRID_COLUMNS,
                "note": _("Each widget's `span` is a share of %s columns. A row "
                          "of four KPIs is span 3 each.") % spec_lib.GRID_COLUMNS,
            },
            "aggregates": sorted(spec_lib.AGGREGATES),
            "date_granularities": sorted(spec_lib.DATE_GRANULARITIES),
            "periods": sorted(spec_lib.PERIODS),
            "filter_types": sorted(spec_lib.FILTER_TYPES),
            "format_kinds": sorted(spec_lib.FORMAT_KINDS),
            "compare": {
                "targets": sorted(spec_lib.COMPARE_TARGETS),
                "comparable_types": sorted(spec_lib.COMPARABLE_TYPES),
                "note": _(
                    "Set `compare` at the top level to give the whole "
                    "dashboard a comparison — the person reading it can change "
                    "or switch it off without editing anything. A widget's own "
                    "`compare` overrides that for one tile, including "
                    "\"none\" to opt out. Only %(ok)s can show one, and only "
                    "when the tile has a date range: a pie of two periods is "
                    "unreadable and a table's rows do not line up.",
                    ok=", ".join(sorted(spec_lib.COMPARABLE_TYPES))),
            },
            "limits": {
                "max_widgets": spec_lib.MAX_WIDGETS,
                "max_domain_terms": spec_lib.MAX_DOMAIN_TERMS,
            },
            "rules": [
                _("Unknown keys are refused, not ignored — so a typo comes "
                  "back as an error you can correct rather than silently "
                  "producing the wrong dashboard."),
                _("Call get_schema on a model before you reference its fields. "
                  "Guessed field names are the most common reason a spec is "
                  "refused."),
                _("A measure must be \"__count\" or \"field:aggregate\" over a "
                  "numeric field."),
                _("An `order` term must be one of that tile's own groupings "
                  "or one of its own measures in full — \"amount_total:sum "
                  "desc\", never \"amount_total desc\". A bare field name "
                  "looks right and is refused."),
                _("Prefer a top-level `compare` over setting it per widget: "
                  "one dashboard-wide comparison is what a person means when "
                  "they ask to see something against last year."),
                _("You can only build over models this connection may already "
                  "read. Call list_models to see them."),
            ],
            "example": self._schema_example(),
        }

    def _schema_example(self):
        return {
            "schema": spec_lib.SCHEMA_ID,
            "title": "Sales overview",
            "description": "Order intake and top customers for the period.",
            "compare": {"to": "previous_year"},
            "filters": [{"key": "period", "type": "date_range",
                         "label": "Period", "default": "this_year"}],
            "widgets": [
                {"id": "total", "type": "kpi", "title": "Order intake",
                 "span": 3,
                 "query": {"model": "sale.order",
                           "domain": [["state", "in", ["sale", "done"]]],
                           "measures": ["amount_total:sum"]},
                 "format": {"kind": "monetary"}},
                {"id": "count", "type": "kpi", "title": "Orders", "span": 3,
                 "query": {"model": "sale.order",
                           "domain": [["state", "in", ["sale", "done"]]],
                           "measures": ["__count"]}},
                {"id": "monthly", "type": "line", "title": "By month",
                 "span": 6,
                 "query": {"model": "sale.order",
                           "domain": [["state", "in", ["sale", "done"]]],
                           "group_by": ["date_order:month"],
                           "measures": ["amount_total:sum"]}},
                {"id": "grid", "type": "pivot",
                 "title": "Customers by month", "span": 12,
                 "query": {"model": "sale.order",
                           "domain": [["state", "in", ["sale", "done"]]],
                           "group_by": ["partner_id", "date_order:month"],
                           "measures": ["amount_total:sum"],
                           "order": "amount_total:sum desc"},
                 "format": {"kind": "monetary"}},
                {"id": "customers", "type": "bar", "title": "Top customers",
                 "span": 12,
                 "query": {"model": "sale.order",
                           "domain": [["state", "in", ["sale", "done"]]],
                           "group_by": ["partner_id"],
                           "measures": ["amount_total:sum"],
                           "order": "amount_total:sum desc", "limit": 10}},
            ],
        }

    def _handler_list_dashboards(self, scope, args):
        dashboards = self.env["ai.dashboard"].search(
            [("state", "=", "published")], limit=100)
        return {"dashboards": [{
            "id": d.id,
            "name": d.name,
            "description": d.description or "",
            "owner": d.owner_id.name,
            "mine": d.owner_id.id == self.env.uid,
            "shared": d.is_shared,
            "widgets": len(d.spec().get("widgets", [])),
        } for d in dashboards]}

    def _handler_get_dashboard(self, scope, args):
        """Return a spec so a change is a diff, not a rebuild."""
        dashboard = self._dashboard(args.get("dashboard_id"))
        return {
            "id": dashboard.id,
            "name": dashboard.name,
            "description": dashboard.description or "",
            "state": dashboard.state,
            "editable": dashboard.owner_id.id == self.env.uid,
            "spec": dashboard.spec(),
            "explanation": dashboard.explanation,
        }

    def _handler_seed_from_view(self, scope, args):
        """A starting point from a model the user is already looking at."""
        model_name = args.get("model")
        self._require_line(scope, model_name, "read")
        model = self.env[model_name]
        meta = model.fields_get(attributes=["type", "string", "store"])

        numeric = [name for name, f in meta.items()
                   if f.get("type") in ("integer", "float", "monetary")
                   and f.get("store") and not name.startswith("_")]
        groupable = [name for name, f in meta.items()
                     if f.get("type") in ("many2one", "selection", "date",
                                          "datetime")
                     and f.get("store") and not name.startswith("_")]
        return {
            "model": model_name,
            "label": self.env["ir.model"]._get(model_name).name,
            "suggested_measures": sorted(numeric)[:12],
            "suggested_group_by": sorted(groupable)[:12],
            "date_fields": sorted(n for n in groupable
                                  if meta[n]["type"] in ("date", "datetime")),
            "note": _("Build a spec from these. Call get_dashboard_schema for "
                      "the format if you have not already."),
        }

    # -------------------------------------------------------------- writing
    def _handler_preview_dashboard(self, scope, args):
        """Validate, save as a preview, and show the assistant its own numbers.

        Deliberately does not publish. Nothing an assistant builds reaches the
        app tile until a person has looked at it — the same drafting-versus-
        committing split that makes the approval gate work.
        """
        spec = self._spec_arg(args)
        dashboard = self.env["ai.dashboard"].create({
            "name": args.get("name") or spec.get("title") or _("Untitled"),
            "description": spec.get("description") or "",
            "spec_json": json.dumps(spec),
            "state": "draft",
            "built_by_ai": True,
        })
        dashboard._notify_ready(
            _("\"%s\" is ready to preview.") % dashboard.name)
        sample = self.env["ai.dashboard.render"].sample(dashboard.spec())
        return {
            "dashboard_id": dashboard.id,
            "name": dashboard.name,
            "state": "draft",
            "url": self._dashboard_url(dashboard),
            "sample": sample,
            "message": str(_(
                "Saved as a preview. It is not on their app tile yet. Tell the "
                "user to open it and click Save if it looks right — check the "
                "sample figures above first, and say so if anything looks "
                "wrong.")),
        }

    def _handler_save_dashboard(self, scope, args):
        """Publish a preview, or update and publish an existing dashboard."""
        dashboard_id = args.get("dashboard_id")
        if dashboard_id:
            dashboard = self._dashboard(dashboard_id, write=True)
            values = {}
            if args.get("spec"):
                values["spec_json"] = json.dumps(self._spec_arg(args))
                values["_version_note"] = args.get("note") or _(
                    "Changed by an AI assistant")
            if args.get("name"):
                values["name"] = args["name"]
            values["state"] = "published"
            dashboard.write(values)
        else:
            spec = self._spec_arg(args)
            dashboard = self.env["ai.dashboard"].create({
                "name": args.get("name") or spec.get("title") or _("Untitled"),
                "description": spec.get("description") or "",
                "spec_json": json.dumps(spec),
                "state": "published",
                "built_by_ai": True,
            })
        dashboard._notify_ready()
        return {
            "dashboard_id": dashboard.id,
            "name": dashboard.name,
            "state": "published",
            "url": self._dashboard_url(dashboard),
            "message": str(_("Saved. It is on their AI Dashboards app tile now.")),
        }

    def _handler_delete_dashboard(self, scope, args):
        dashboard = self._dashboard(args.get("dashboard_id"), write=True)
        name = dashboard.name
        dashboard.unlink()
        return {"deleted": True, "name": name,
                "message": str(_("\"%s\" was deleted.") % name)}

    # -------------------------------------------------------------- helpers
    def _spec_arg(self, args):
        """Pull the spec out of the arguments, however the client sent it."""
        spec = args.get("spec")
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                raise UserError(_(
                    "`spec` must be a JSON object. Call get_dashboard_schema "
                    "for the format."))
        if not isinstance(spec, dict) or not spec:
            raise UserError(_(
                "This tool needs a `spec` object. Call get_dashboard_schema "
                "first — it returns the format and a worked example."))
        # Validated here as well as in ai.dashboard.write, so a refusal reaches
        # the assistant as a tool error it can act on rather than as a
        # traceback from the ORM.
        return spec_lib.validate(spec, self.env, self._dashboard_scope())

    @api.model
    def _dashboard_scope(self):
        return self.env.user.sudo().mcp_effective_scope()

    def _dashboard(self, dashboard_id, write=False):
        if not dashboard_id:
            raise UserError(_("This tool needs a `dashboard_id`. Call "
                              "list_dashboards to find one."))
        dashboard = self.env["ai.dashboard"].browse(int(dashboard_id)).exists()
        if not dashboard:
            raise UserError(_("There is no dashboard with id %s.")
                            % dashboard_id)
        dashboard.check_access("read")
        if write and dashboard.owner_id.id != self.env.uid \
                and not self.env.user.has_group(
                    "ai_dashboards.group_dashboard_admin"):
            raise AccessError(_(
                "\"%(name)s\" belongs to %(owner)s. Suggest duplicating it "
                "instead — the copy is theirs to change.",
                name=dashboard.name, owner=dashboard.owner_id.name))
        return dashboard

    def _dashboard_url(self, dashboard):
        """A link the user can actually click.

        Uses the parent module's public-address resolution rather than
        web.base.url: behind a TLS-terminating proxy that parameter routinely
        holds an http:// spelling of the right host, and a link an assistant
        hands someone has to work on the first click.
        """
        base = public_base_url(self.env)
        return "%s/odoo/action-ai_dashboards.action_dashboards/%s" % (
            base, dashboard.id)


class MCPTool(models.Model):
    _inherit = "mcp.tool"

    handler = fields.Selection(
        selection_add=[
            ("get_dashboard_schema", "Dashboard: describe the format"),
            ("list_dashboards", "Dashboard: list saved dashboards"),
            ("get_dashboard", "Dashboard: read one specification"),
            ("seed_from_view", "Dashboard: suggest fields for a model"),
            ("preview_dashboard", "Dashboard: build a preview"),
            ("save_dashboard", "Dashboard: save and publish"),
            ("delete_dashboard", "Dashboard: delete"),
        ],
        ondelete={
            "get_dashboard_schema": "cascade",
            "list_dashboards": "cascade",
            "get_dashboard": "cascade",
            "seed_from_view": "cascade",
            "preview_dashboard": "cascade",
            "save_dashboard": "cascade",
            "delete_dashboard": "cascade",
        },
    )

    @api.model
    def _write_handlers(self):
        """Register the verbs here that change data.

        Getting this wrong is the failure this extension point exists to
        prevent: an unregistered writing handler computes writes=False, so it
        is advertised to read-only scopes and executes for connections that
        were never granted odoo:write. mcp_governance_suite's
        tests/test_permissions.py fails if any handler is unclassified.
        """
        return super()._write_handlers() | DASHBOARD_WRITE_HANDLERS
