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
    def list_tools(self, scope, granted_scopes=None):
        # Governance config is read with elevated rights (it is not sensitive
        # business data); actual record access below always runs as the user.
        scope = scope.sudo()
        # Don't advertise what this connection cannot use: a token granted only
        # odoo:read should not see write tools at all, or the model will keep
        # trying them and collecting 403s.
        writes_allowed = granted_scopes is None or SCOPE_WRITE in granted_scopes
        tools = []
        for cap in scope.allowed_capabilities():
            for tool in cap.tool_ids.filtered("active"):
                if tool.writes and (scope.read_only or not writes_allowed):
                    continue  # never advertise mutating tools we would refuse
                tools.append({
                    "name": tool.name,
                    "title": tool.title or tool.name.replace("_", " ").title(),
                    "description": tool.description,
                    "inputSchema": self._input_schema(tool),
                })
        return tools

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
            # Carry the auth source so approval requests stay attributable.
            engine = self.with_context(mcp_api_key_id=audit_ctx.get("api_key_id"))
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
                _("'%s' modifies records and needs the %s scope.")
                % (tool.name, SCOPE_WRITE))

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
                "under Fleet AI > Model Permissions.",
                model=model, scope=scope.name))
        if not line["can_%s" % op]:
            raise AccessError(_(
                "The '%(scope)s' matrix does not allow %(op)s on '%(model)s'. "
                "An administrator can enable the '%(op)s' switch for that model "
                "under Fleet AI > Model Permissions.",
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
        caps = []
        for cap in scope.allowed_capabilities():
            tools = cap.tool_ids.filtered(
                lambda t: t.active and not (scope.read_only and t.writes))
            caps.append({
                "name": cap.name,
                "technical_name": cap.technical_name,
                "description": cap.description,
                "tools": [{"name": t.name, "description": t.description} for t in tools],
            })
        return {"read_only": scope.read_only, "capabilities": caps}

    def _handler_list_models(self, scope, args):
        return {"models": [{
            "model": l.model_name,
            "name": l.model_id.name,
            "read": l.can_read, "create": l.can_create,
            "write": l.can_write, "unlink": l.can_unlink,
        } for l in scope.line_ids]}

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
        model = args["model"]
        line = self._require_line(scope, model, "read")
        blacklist = line.blacklisted_fields()
        domain = self._parse_domain(args.get("domain")) + self._scope_domain(line)
        requested = args.get("fields") or ["display_name"]
        field_list = [f for f in requested if f not in blacklist]
        limit = self._clamp_limit(scope, args.get("limit"))
        offset = max(0, int(args.get("offset") or 0))
        records = self.env[model].with_context(**self._company_ctx(args)).search_read(
            domain, field_list, limit=limit, offset=offset, order=args.get("order"))
        return {"model": model, "count": len(records), "records": records}

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
        res = self.env[model].with_context(**self._company_ctx(args)).name_search(
            name=args.get("name", ""), args=domain, limit=limit)
        return {"model": model, "results": [{"id": i, "name": n} for i, n in res]}

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
            order=args.get("order"), limit=limit)
        groups = []
        for row in rows:
            entry, idx = {}, 0
            for key in groupby:
                entry[key] = self._jsonify(row[idx]); idx += 1
            for key in aggregates:
                entry[key] = self._jsonify(row[idx]); idx += 1
            groups.append(entry)
        return {"model": model, "group_by": groupby,
                "measures": aggregates, "groups": groups}

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
                "must list the exact methods under Fleet AI > Model "
                "Permissions.", model=model))
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
        return {"approval_required": True, "approval_id": req.id,
                "message": _("Queued for human approval.")}

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
