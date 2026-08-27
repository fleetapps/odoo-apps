# -*- coding: utf-8 -*-
"""Tool execution engine - every call is scope-checked, ACL-checked, audited.

The controller has already switched ``self.env`` to the acting user, so native
Odoo security (ir.model.access + ir.rule + field groups + multi-company record
rules) is enforced *underneath* everything here. The engine adds the governance
layer on top: capability gating, per-model operation scopes, field blacklists,
extra record domains, row caps, rate limiting and approval gates.

Handlers are generic verbs selected by data (see mcp.tool.handler), so partners
extend the connector with new tool *records*, or override/add a `_handler_*`
method by inheriting this model - no controller changes, upgrade-safe.

ORM reference (search_read / _read_group / name_search / fields_get / domains):
https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html
"""
import ast
import datetime
import json
import logging
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .mcp_scope import DENIED_METHODS

_logger = logging.getLogger(__name__)
MAX_ARGS_LOG = 4000

# Read verbs whose usefulness depends on *which* models this connection can
# reach, so their descriptions name them (see _tool_description).
MODEL_AWARE_HANDLERS = {
    "search_records", "count_records", "name_search", "read_group",
}
# How many model names to spell out before summarising the rest. Long enough to
# cover a normal scope, short enough not to bloat every tool description.
MODELS_IN_DESCRIPTION = 12

# OAuth scope names, duplicated from the controller to keep the model layer
# import-free of controllers.
SCOPE_READ = "odoo:read"
SCOPE_WRITE = "odoo:write"


class MCPInsufficientScope(Exception):
    """The token is valid but its OAuth scope is too narrow for this call.

    Distinct from AccessError on purpose: this is a 403 the client can *fix*
    by stepping up its authorization, so the controller has to answer with a
    scope challenge rather than a generic tool error.
    """

    def __init__(self, required, description=None):
        self.required = list(required)
        self.description = description
        super().__init__(description or "insufficient_scope")


class MCPEngine(models.AbstractModel):
    _name = "mcp.engine"
    _description = "MCP Engine"

    # ================================================================ tools/list
    @api.model
    def list_tools(self, scope):
        """Advertise every tool this *governance scope* permits.

        Deliberately not filtered by the token's OAuth scope. A tool the client
        cannot see is a tool it can never be told to ask permission for, and
        hiding write tools from a read-scoped token is what silently strands a
        connection: the client pins the scope set from our 401 challenge, never
        sees a write tool, and so never trips the step-up flow that MCP defines
        for exactly this situation. Calling one without ``odoo:write`` answers
        403 ``insufficient_scope`` naming the scope to request, which is how a
        client is supposed to widen its authorization.

        ``scope.read_only`` is a different thing: it is the administrator's
        kill switch, no amount of re-authorization can satisfy it, so those
        tools stay hidden rather than dangled.
        """
        # Governance config is read with elevated rights (it is not sensitive
        # business data); actual record access below always runs as the user.
        scope = scope.sudo()
        hint = self._model_hint(scope)
        tools = []
        for cap in scope.allowed_capabilities():
            for tool in cap.tool_ids.filtered("active"):
                if tool.writes and scope.read_only:
                    continue  # no re-authorization can unlock these
                tools.append({
                    "name": tool.name,
                    "title": tool.title or tool.name.replace("_", " ").title(),
                    "description": self._tool_description(tool, hint),
                    "inputSchema": self._input_schema(tool),
                    "annotations": self._annotations(tool),
                })
        return tools

    # ------------------------------------------------- per-scope descriptions
    def _model_hint(self, scope):
        """Name the models this scope can read, for the read tools to carry.

        Without it every connection is advertised the same generic description
        and the assistant has to spend a ``list_capabilities`` (then usually a
        ``list_models``) round trip before it can answer the first question -
        which the user experiences as the connector being slow to wake up.

        Varying *tool* output by the presented authorization is explicitly
        permitted (MCP 2026-07-28 tools/list: "The set MAY vary by the
        authorization presented on the request"), and the same section asks for
        a deterministic order, which the sort below provides. Note this must
        never move into ``server/discover``: that result is returned with
        ``cacheScope: "public"`` and would be shared across users.
        """
        names = scope.readable_model_names()
        if not names:
            return ""
        shown = names[:MODELS_IN_DESCRIPTION]
        listed = ", ".join(shown)
        if len(names) > len(shown):
            return str(_(
                " Models readable on this connection include %(models)s and "
                "%(more)s more — call list_models for the full set.",
                models=listed, more=len(names) - len(shown)))
        return str(_(" Models readable on this connection: %s.") % listed)

    def _tool_description(self, tool, hint):
        """The description as the client sees it: generic text plus this scope."""
        if hint and tool.handler in MODEL_AWARE_HANDLERS:
            return "%s%s" % (tool.description, hint)
        return tool.description

    def _annotations(self, tool):
        """MCP tool behaviour hints (2026-07-28 ToolAnnotations).

        Clients use these to decide when to put a human in the loop, which is
        this module's entire pitch, so getting them right is on-brand rather
        than cosmetic. ``destructiveHint`` and ``idempotentHint`` are defined
        as meaningful only when ``readOnlyHint`` is false, so they are omitted
        for read tools rather than sent as noise. ``openWorldHint`` is false
        throughout: every tool here acts on this one Odoo database and nothing
        outside it.
        """
        read_only = not tool.writes
        annotations = {"readOnlyHint": read_only, "openWorldHint": False}
        if not read_only:
            annotations["destructiveHint"] = tool.handler in (
                "unlink_record", "call_method")
            annotations["idempotentHint"] = tool.handler == "write_record"
        return annotations

    def _input_schema(self, tool):
        try:
            schema = json.loads(tool.input_schema or "")
        except (ValueError, TypeError):
            schema = None
        if not isinstance(schema, dict) or not schema:
            return {"type": "object", "properties": {}}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        return schema

    # ================================================================ tools/call
    @api.model
    def call_tool(self, scope, name, args, audit_ctx=None):
        audit_ctx = audit_ctx or {}
        args = args or {}
        scope = scope.sudo()  # config reads only; data ops stay as the user
        start = time.time()
        status, payload = "ok", None
        model_used = args.get("model") if isinstance(args, dict) else None
        try:
            tool = self._resolve_tool(scope, name)
            self._check_oauth_scope(tool, audit_ctx)
            self._check_rate_limit(scope, audit_ctx)
            # Carry the auth source so approval requests stay attributable, and
            # the granted OAuth scopes so a handler can explain what is missing.
            engine = self.with_context(
                mcp_api_key_id=audit_ctx.get("api_key_id"),
                mcp_granted_scopes=audit_ctx.get("granted_scopes"))
            payload = getattr(engine, f"_handler_{tool.handler}")(scope, args)
        except MCPInsufficientScope:
            # Audit the refusal, then let the controller turn it into a 403
            # scope challenge rather than a generic tool error.
            self._audit(scope, name, model_used, args, "denied", start,
                        {"error": "insufficient_scope"}, audit_ctx)
            raise
        except (AccessError, UserError) as exc:
            status, payload = "error", {"error": type(exc).__name__, "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - audited, then surfaced generically
            _logger.exception("MCP tool %s crashed", name)
            status, payload = "error", {"error": "InternalError", "message": str(exc)}
        self._audit(scope, name, model_used, args, status, start, payload, audit_ctx)
        result = {
            "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
            "isError": status == "error",
        }
        if status == "ok" and isinstance(payload, dict):
            result["structuredContent"] = payload  # MCP 2025-06-18 structured output
        return result

    def _resolve_tool(self, scope, name):
        tool = self.env["mcp.tool"].sudo().search(
            [("name", "=", name), ("active", "=", True)], limit=1)
        if not tool or tool.capability_id not in scope.allowed_capabilities():
            raise AccessError(_("Tool '%s' is not available in this scope.") % name)
        if scope.read_only and tool.writes:
            raise AccessError(_("Tool '%s' is blocked: this connection is read-only.") % name)
        return tool

    # ============================================================= oauth scopes
    def _check_oauth_scope(self, tool, audit_ctx):
        """Gate mutating tools on the OAuth scope the user actually granted.

        This is the layer that makes a step-up flow meaningful: a connection
        authorized read-only gets a 403 naming ``odoo:write`` instead of a flat
        denial, so the client knows what to ask for. API-key connections carry
        no OAuth scope and are governed by mcp.scope alone.
        """
        granted = audit_ctx.get("granted_scopes")
        if granted is None:
            return
        if tool.writes and SCOPE_WRITE not in granted:
            raise MCPInsufficientScope(
                [SCOPE_WRITE],
                _("'%(tool)s' changes records, so this connection needs the "
                  "'%(scope)s' scope and was only granted %(granted)s. "
                  "Re-authorize the connector and keep 'Let it create and "
                  "update records' ticked on the Odoo consent screen.",
                  tool=tool.name, scope=SCOPE_WRITE,
                  granted=", ".join(granted) or "nothing"))

    # =============================================================== rate limit
    def _check_rate_limit(self, scope, audit_ctx):
        if not scope.rate_limit_per_hour:
            return
        since = fields.Datetime.now() - datetime.timedelta(hours=1)
        domain = [("create_date", ">=", since)]
        if audit_ctx.get("api_key_id"):
            domain.append(("api_key_id", "=", audit_ctx["api_key_id"]))
        elif audit_ctx.get("oauth_token_id"):
            domain.append(("oauth_token_id", "=", audit_ctx["oauth_token_id"]))
        else:
            domain.append(("user_id", "=", self.env.uid))
        used = self.env["mcp.audit.log"].sudo().search_count(domain)
        if used >= scope.rate_limit_per_hour:
            raise UserError(_(
                "Rate limit reached (%s calls/hour). Try again shortly."
            ) % scope.rate_limit_per_hour)

    # =============================================================== enforcement
    # Human labels for the matrix columns, so a refusal names the switch the
    # admin actually has to flip rather than an internal field name.
    _OP_LABELS = {
        "read": "Read", "create": "Create", "write": "Update",
        "unlink": "Delete", "call_methods": "Method Calls",
    }

    def _require_line(self, scope, model, op):
        """Resolve the matrix row for this model, or refuse with a fix.

        The error text is deliberately specific: the most common complaint
        about ERP MCP servers is an opaque "access denied" that tells neither
        the model nor the assistant what to do about it.
        """
        if model not in self.env:
            raise AccessError(_(
                "There is no model named '%s' in this database.") % model)
        line = scope.line_for_model(model)
        label = self._OP_LABELS.get(op, op)
        if not line:
            raise AccessError(_(
                "'%(model)s' is not in the '%(scope)s' permission matrix, so "
                "no AI access to it is configured. An administrator can add it "
                "under Odoo MCP → Permissions → Model Permissions.",
                model=model, scope=scope.name))
        if not line["can_%s" % op]:
            raise AccessError(_(
                "The '%(scope)s' matrix does not allow %(op)s on '%(model)s'. "
                "An administrator can enable the '%(op)s' switch for that model "
                "under Odoo MCP → Permissions → Model Permissions.",
                scope=scope.name, op=label, model=model))
        return line

    def _scope_domain(self, line):
        return self._parse_domain(line.record_domain or "[]")

    @staticmethod
    def _parse_domain(raw):
        if not raw:
            return []
        if isinstance(raw, (list, tuple)):
            return list(raw)
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            raise UserError(_("Invalid domain: %s") % raw)
        return list(parsed) if isinstance(parsed, (list, tuple)) else []

    def _clamp_limit(self, scope, requested):
        cap = scope.max_records or 200
        try:
            requested = int(requested) if requested else cap
        except (ValueError, TypeError):
            requested = cap
        return max(1, min(requested, cap))

    @staticmethod
    def _jsonify(value):
        if isinstance(value, models.BaseModel):
            if len(value) == 1:
                return {"id": value.id, "name": value.display_name}
            return [{"id": r.id, "name": r.display_name} for r in value]
        if isinstance(value, datetime.datetime):
            return fields.Datetime.to_string(value)
        if isinstance(value, datetime.date):
            return fields.Date.to_string(value)
        return value

    def _company_ctx(self, args):
        """Honour multi-company: default to all the user's companies, or a
        client-requested subset (never wider than what the user is allowed)."""
        allowed = self.env.user.company_ids.ids
        requested = args.get("company_ids")
        if requested:
            requested = [c for c in requested if c in allowed]
        return {"allowed_company_ids": requested or allowed}

    # ================================================================= handlers
    def _handler_list_capabilities(self, scope, args):
        """Describe what this connection can do - and why anything is missing.

        A capability that comes back with an empty tool list and no explanation
        reads exactly like a broken connector, so every hidden or unusable tool
        says which switch would restore it and who can flip it.
        """
        granted = self.env.context.get("mcp_granted_scopes")
        write_granted = granted is None or SCOPE_WRITE in granted
        # The second place tool descriptions are emitted; it has to say the
        # same thing tools/list does or the two drift apart.
        hint = self._model_hint(scope)
        caps = []
        for cap in scope.allowed_capabilities():
            active = cap.tool_ids.filtered("active")
            # Only the read-only kill switch actually removes tools; a missing
            # OAuth scope leaves them listed but unusable until re-authorized.
            hidden = active.filtered("writes") if scope.read_only else active.browse()
            shown = active - hidden
            entry = {
                "name": cap.name,
                "technical_name": cap.technical_name,
                "description": cap.description,
                "tools": [{"name": t.name,
                           "description": self._tool_description(t, hint)}
                          for t in shown],
            }
            # str() on purpose: these travel in structuredContent, and a lazy
            # translation object there would only resolve via a serializer
            # fallback.
            if hidden:
                entry["unavailable_reason"] = str(_(
                    "%(count)s tool(s) in this capability are hidden because "
                    "the '%(scope)s' governance scope has Read Only switched "
                    "on. Only an Odoo administrator can change that, under "
                    "Odoo MCP → Permissions → Scopes; re-authorizing "
                    "will not help.",
                    count=len(hidden), scope=scope.name))
            elif not write_granted and shown.filtered("writes"):
                entry["needs_authorization"] = str(_(
                    "These tools are listed but will answer "
                    "'insufficient_scope' until this connection is "
                    "re-authorized with the '%(scope)s' scope. Reconnect the "
                    "connector and keep 'Let it create and update records' "
                    "ticked on the Odoo consent screen.", scope=SCOPE_WRITE))
            caps.append(entry)
        return {
            "scope": scope.name,
            "read_only": scope.read_only,
            # None means an API key connection, which carries no OAuth scope
            # and is governed by the scope above alone.
            "granted_scopes": granted,
            "approval_required": scope.require_approval and not scope.read_only,
            "capabilities": caps,
        }

    def _handler_list_models(self, scope, args):
        """What the matrix currently permits, per model.

        Archived rows are excluded: ``line_for_model`` already ignores them at
        call time, so advertising one here would promise the AI access that
        every subsequent call refuses. Method calls are reported too, with the
        allow-list, because guessing a method name and being refused is a
        round trip the assistant should never have to spend.
        """
        models_out = []
        for line in scope.line_ids.filtered("active"):
            entry = {
                "model": line.model_name,
                "name": line.model_id.name,
                "read": line.can_read, "create": line.can_create,
                "write": line.can_write, "unlink": line.can_unlink,
                "call_methods": line.can_call_methods,
            }
            if line.can_call_methods:
                entry["allowed_methods"] = sorted(line.allowed_method_set())
            models_out.append(entry)
        return {"models": models_out}

    def _handler_get_schema(self, scope, args):
        model = args["model"]
        line = self._require_line(scope, model, "read")
        blacklist = line.blacklisted_fields()
        raw = self.env[model].fields_get(attributes=[
            "string", "type", "help", "relation", "required", "readonly", "selection"])
        out = {}
        for fname, meta in raw.items():
            if fname in blacklist:
                continue
            entry = {k: meta[k] for k in ("string", "type", "help", "relation",
                                          "required", "readonly")
                     if meta.get(k) not in (None, "")}
            if meta.get("selection"):
                entry["selection"] = [list(opt) for opt in meta["selection"]]
            out[fname] = entry
        return {"model": model, "fields": out}

    def _handler_get_business_context(self, scope, args):
        allowed_models = set(scope.line_ids.mapped("model_name"))
        domain = [("active", "=", True)]
        model = args.get("model")
        if model:
            if model not in allowed_models:
                raise AccessError(_("Scope denies read on %s.") % model)
            domain.append(("model_name", "=", model))
        else:
            # General context (no model) + context for in-scope models only.
            domain += ["|", ("model_name", "=", False),
                       ("model_name", "in", list(allowed_models))]
        ctx = self.env["mcp.context"].sudo().search(domain)
        return {"context": ctx.as_payload()}

    def _handler_search_records(self, scope, args):
        """Read records, and say plainly when there are more of them.

        The row cap is a real protection - it is what stops an AI query on a
        100k-record model from taking the database with it - but a silent cap
        is worse than no cap: the assistant receives a full page, has nothing
        to tell it the page was full, and reports partial data to the user as
        if it were the whole answer. So fetch one row past the cap, return the
        cap's worth, and hand back `has_more` so the assistant knows to
        paginate, narrow the domain, or aggregate with read_group instead.
        """
        model = args["model"]
        line = self._require_line(scope, model, "read")
        blacklist = line.blacklisted_fields()
        domain = self._parse_domain(args.get("domain")) + self._scope_domain(line)
        requested = args.get("fields") or ["display_name"]
        field_list = [f for f in requested if f not in blacklist]
        limit = self._clamp_limit(scope, args.get("limit"))
        offset = max(0, int(args.get("offset") or 0))
        records = self.env[model].with_context(**self._company_ctx(args)).search_read(
            domain, field_list, limit=limit + 1, offset=offset,
            order=args.get("order"))
        has_more = len(records) > limit
        records = records[:limit]
        return {"model": model, "count": len(records), "limit": limit,
                "offset": offset, "has_more": has_more, "records": records}

    def _handler_count_records(self, scope, args):
        model = args["model"]
        line = self._require_line(scope, model, "read")
        domain = self._parse_domain(args.get("domain")) + self._scope_domain(line)
        count = self.env[model].with_context(**self._company_ctx(args)).search_count(domain)
        return {"model": model, "count": count}

    def _handler_name_search(self, scope, args):
        model = args["model"]
        line = self._require_line(scope, model, "read")
        domain = self._parse_domain(args.get("domain")) + self._scope_domain(line)
        limit = self._clamp_limit(scope, args.get("limit"))
        # `domain=`, not the pre-19 `args=`: Odoo 19 renamed the parameter and
        # kept no alias, so the old spelling raised TypeError and this tool -
        # the one the business context tells the AI to use before every
        # filter-by-name - failed with an opaque internal error every time.
        res = self.env[model].with_context(**self._company_ctx(args)).name_search(
            name=args.get("name", ""), domain=domain, limit=limit + 1)
        has_more = len(res) > limit
        res = res[:limit]
        # has_more matters here too: a truncated match list is how an assistant
        # picks "the wrong Acme" and writes to it with complete confidence.
        return {"model": model, "count": len(res), "limit": limit,
                "has_more": has_more,
                "results": [{"id": i, "name": n} for i, n in res]}

    def _handler_read_group(self, scope, args):
        model = args["model"]
        line = self._require_line(scope, model, "read")
        blacklist = line.blacklisted_fields()
        domain = self._parse_domain(args.get("domain")) + self._scope_domain(line)
        groupby = args.get("group_by") or args.get("groupby") or []
        if isinstance(groupby, str):
            groupby = [groupby]
        groupby = [g for g in groupby if g.split(":")[0] not in blacklist]
        measures = args.get("measures") or ["__count"]
        if isinstance(measures, str):
            measures = [measures]
        aggregates = []
        for measure in measures:
            if measure == "__count":
                aggregates.append("__count")
            elif measure.split(":")[0] not in blacklist:
                aggregates.append(measure if ":" in measure else "%s:sum" % measure)
        limit = self._clamp_limit(scope, args.get("limit"))
        rows = self.env[model].with_context(**self._company_ctx(args))._read_group(
            domain, groupby=groupby, aggregates=aggregates,
            order=args.get("order"), limit=limit + 1)
        # Same trap as search_records, and worse here: a report silently missing
        # its last groups still totals up and still looks complete.
        has_more = len(rows) > limit
        rows = rows[:limit]
        groups = []
        for row in rows:
            entry, idx = {}, 0
            for key in groupby:
                entry[key] = self._jsonify(row[idx]); idx += 1
            for key in aggregates:
                entry[key] = self._jsonify(row[idx]); idx += 1
            groups.append(entry)
        return {"model": model, "group_by": groupby, "measures": aggregates,
                "count": len(groups), "limit": limit, "has_more": has_more,
                "groups": groups}

    # ---------------------------------------------------------- writes (gated)
    def _handler_create_record(self, scope, args):
        model, values = args["model"], dict(args.get("values") or {})
        line = self._require_line(scope, model, "create")
        self._strip_blacklist(line, values)
        if scope.require_approval:
            return self._queue_approval(scope, "create", model, 0, values, args)
        record = self.env[model].create(values)
        return {"id": record.id, "display_name": record.display_name}

    def _handler_write_record(self, scope, args):
        model = args["model"]
        rid = int(args["record_id"])
        values = dict(args.get("values") or {})
        line = self._require_line(scope, model, "write")
        self._strip_blacklist(line, values)
        self._assert_in_domain(scope, line, model, rid)
        if scope.require_approval:
            return self._queue_approval(scope, "write", model, rid, values, args)
        self.env[model].browse(rid).write(values)
        return {"id": rid, "written": True}

    def _handler_unlink_record(self, scope, args):
        model = args["model"]
        rid = int(args["record_id"])
        line = self._require_line(scope, model, "unlink")
        self._assert_in_domain(scope, line, model, rid)
        if scope.require_approval:
            return self._queue_approval(scope, "unlink", model, rid, {}, args)
        self.env[model].browse(rid).unlink()
        return {"id": rid, "deleted": True}

    def _handler_call_method(self, scope, args):
        """Invoke an allow-listed business method (confirm, post, send...).

        This is the most dangerous verb in the connector, so it is gated four
        times over: the matrix bit, an explicit per-model allow-list of method
        names, a global denylist of ORM/privilege verbs, and the acting user's
        own Odoo rights when the method runs. The allow-list is the real gate -
        an empty one permits nothing even with the bit switched on.
        """
        model = args["model"]
        method = (args.get("method") or "").strip()
        line = self._require_line(scope, model, "call_methods")
        allowed = line.allowed_method_set()
        if not allowed:
            raise AccessError(_(
                "Method calls are enabled for '%(model)s' but no method names "
                "are allow-listed, so nothing can be called. An administrator "
                "must list the exact methods under Odoo MCP → "
                "Permissions → Model Permissions.", model=model))
        if method not in allowed:
            raise AccessError(_(
                "'%(method)s' is not allow-listed on '%(model)s'. Permitted "
                "here: %(allowed)s.",
                method=method, model=model, allowed=", ".join(sorted(allowed))))
        # Belt and braces: the constraint blocks these at save time, but the
        # allow-list is data and data can be loaded from anywhere.
        if method.startswith("_") or method in DENIED_METHODS:
            raise AccessError(
                _("'%s' can never be called over MCP.") % method)

        record_ids = args.get("record_ids") or []
        if isinstance(record_ids, int):
            record_ids = [record_ids]
        record_ids = [int(r) for r in record_ids]
        for rid in record_ids:
            self._assert_in_domain(scope, line, model, rid)

        # Pass through only plain keyword arguments. `context` is excluded so a
        # call cannot smuggle in flags that change how the ORM behaves.
        kwargs = {k: v for k, v in (args.get("kwargs") or {}).items()
                  if isinstance(k, str) and not k.startswith("_") and k != "context"}

        if scope.require_approval:
            return self._queue_approval(
                scope, "call_method", model, record_ids[0] if record_ids else 0,
                {"method": method, "record_ids": record_ids, "kwargs": kwargs}, args)

        records = self.env[model].browse(record_ids)
        if record_ids and len(records.exists()) != len(record_ids):
            raise UserError(_("One or more records no longer exist."))
        result = getattr(records, method)(**kwargs)
        return {"model": model, "method": method, "record_ids": record_ids,
                "result": self._jsonify_result(result)}

    @staticmethod
    def _jsonify_result(value):
        """Business methods return anything - an action dict, a bool, records."""
        if isinstance(value, models.BaseModel):
            return [{"id": r.id, "name": r.display_name} for r in value]
        if isinstance(value, dict):
            # Typically an ir.actions.* dict; keep it small and non-executable.
            return {k: v for k, v in value.items()
                    if k in ("type", "name", "res_model", "res_id", "view_mode")}
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    def _assert_in_domain(self, scope, line, model, rid):
        """A write/unlink target must satisfy the scope's record_domain too."""
        domain = [("id", "=", rid)] + self._scope_domain(line)
        if not self.env[model].search_count(domain):
            raise AccessError(_("Record %s of %s is outside this scope.") % (rid, model))

    def _strip_blacklist(self, line, values):
        for field in line.blacklisted_fields():
            values.pop(field, None)

    def _queue_approval(self, scope, op, model, rid, values, args):
        req = self.env["mcp.approval.request"].sudo().create({
            "user_id": self.env.uid,
            "scope_id": scope.id,
            "api_key_id": self.env.context.get("mcp_api_key_id"),
            "operation": op,
            "model_name": model,
            "record_id": rid or 0,
            "values_json": json.dumps(values, default=str),
        })
        req._notify_approvers()
        # str() on purpose: this travels in structuredContent, where a lazy
        # translation object would only resolve via a serializer fallback.
        # Name where the request went, too - "queued for approval" with no
        # destination is how a request sits untouched for a week.
        return {"approval_required": True, "approval_id": req.id,
                "message": str(_(
                    "Nothing has changed yet. This is waiting for a person to "
                    "approve it in Odoo — request #%(id)s, under Odoo MCP → "
                    "Approvals. Tell the user it needs that sign-off before "
                    "anything happens.", id=req.id))}

    # ============================================================ prompts (MCP)
    @api.model
    def list_prompts(self, scope):
        scope = scope.sudo()
        caps = scope.allowed_capabilities()
        prompts = self.env["mcp.prompt"].sudo().search([("active", "=", True)])
        prompts = prompts.filtered(
            lambda p: not p.capability_id or p.capability_id in caps)
        return [{
            "name": p.name,
            "title": p.title or p.name,
            "description": p.description,
            "arguments": p.arguments(),
        } for p in prompts]

    @api.model
    def get_prompt(self, scope, name, arguments):
        prompt = self.env["mcp.prompt"].sudo().search(
            [("name", "=", name), ("active", "=", True)], limit=1)
        if not prompt:
            raise UserError(_("Unknown prompt '%s'.") % name)
        return {
            "description": prompt.description,
            "messages": [{
                "role": "user",
                "content": {"type": "text", "text": prompt.render(arguments)},
            }],
        }

    # ========================================================== resources (MCP)
    @api.model
    def list_resources(self, scope):
        scope = scope.sudo()
        allowed_models = set(scope.line_ids.mapped("model_name"))
        ctx = self.env["mcp.context"].sudo().search([("active", "=", True)])
        ctx = ctx.filtered(lambda c: not c.model_name or c.model_name in allowed_models)
        return [{
            "uri": "odoo://business-context/%s" % c.id,
            "name": c.name,
            "description": _("Business context for %s") % (c.model_name or "the company"),
            "mimeType": "text/markdown",
        } for c in ctx]

    @api.model
    def read_resource(self, scope, uri):
        scope = scope.sudo()
        if not uri.startswith("odoo://business-context/"):
            raise UserError(_("Unknown resource '%s'.") % uri)
        ctx = self.env["mcp.context"].sudo().browse(
            int(uri.rsplit("/", 1)[-1])).exists()
        if not ctx or not ctx.active:
            raise UserError(_("Resource not found."))
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": ctx.body}]}

    # ==================================================================== audit
    def _audit(self, scope, name, model_used, args, status, start, payload, audit_ctx):
        self.env["mcp.audit.log"].sudo().create({
            "api_key_id": audit_ctx.get("api_key_id"),
            "oauth_token_id": audit_ctx.get("oauth_token_id"),
            "user_id": self.env.uid,
            "scope_id": scope.id,
            "tool": name,
            "model_name": model_used,
            "transport": audit_ctx.get("transport", "http"),
            "remote_addr": audit_ctx.get("remote_addr"),
            "args_json": json.dumps(args, default=str)[:MAX_ARGS_LOG],
            "status": status,
            "duration_ms": int((time.time() - start) * 1000),
            "tokens_est": max(1, len(json.dumps(payload or {}, default=str)) // 4),
        })
