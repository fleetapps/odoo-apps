# -*- coding: utf-8 -*-
"""Tool execution engine. Every call is scope-checked, ACL-checked, audited.

ORM reference (search/read/create/write, domains):
https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html
"""
import ast
import json
import time
from datetime import timedelta
from odoo import api, fields, models
from odoo.exceptions import AccessError


class MCPEngine(models.AbstractModel):
    _name = "mcp.engine"
    _description = "MCP Engine"

    # ------------------------------------------------------------------ tools
    @api.model
    def list_tools(self, key):
        scope = key.scope_id
        tools = [
            {"name": "list_models",
             "description": "List models this connection may access.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "search_records",
             "description": "Search records of a model with an Odoo domain.",
             "inputSchema": {"type": "object", "properties": {
                 "model": {"type": "string"},
                 "domain": {"type": "string"},
                 "fields": {"type": "array", "items": {"type": "string"}},
                 "limit": {"type": "integer"}},
                 "required": ["model"]}},
        ]
        if not scope.read_only:
            tools += [
                {"name": "create_record", "description":
                    "Create a record (may require human approval).",
                 "inputSchema": {"type": "object", "properties": {
                     "model": {"type": "string"}, "values": {"type": "object"}},
                     "required": ["model", "values"]}},
                {"name": "write_record", "description":
                    "Update a record (may require human approval).",
                 "inputSchema": {"type": "object", "properties": {
                     "model": {"type": "string"}, "record_id": {"type": "integer"},
                     "values": {"type": "object"}},
                     "required": ["model", "record_id", "values"]}},
            ]
        return tools

    @api.model
    def call_tool(self, key, name, args):
        start = time.time()
        status, payload = "ok", None
        try:
            self._check_rate_limit(key)
            handler = getattr(self, f"_tool_{name}", None)
            if not handler:
                raise AccessError(f"Unknown tool {name}")
            payload = handler(key, args)
        except Exception as e:  # noqa: BLE001 - audited then surfaced
            status, payload = "error", {"error": str(e)}
        if self.env["mcp.config"].get("log_requests", True, cast=bool):
            self.env["mcp.audit.log"].sudo().create({
                "api_key_id": key.id, "user_id": key.user_id.id, "tool": name,
                "args_json": json.dumps(args)[:4000], "status": status,
                "duration_ms": int((time.time() - start) * 1000),
                "tokens_est": max(1, len(json.dumps(payload or {})) // 4),
            })
        key.sudo().last_used = fields.Datetime.now()
        return {"content": [{"type": "text",
                             "text": json.dumps(payload, default=str)}],
                "isError": status == "error"}

    # ------------------------------------------------------------- internals
    def _check_rate_limit(self, key):
        """Per-key calls/hour ceiling.

        `mcp.scope.rate_limit_per_hour` existed as a field but was never read,
        so the limit an admin typed in did nothing. The count comes from the
        audit log, which means the ceiling only holds while request logging is
        on - the Settings help text says so.
        """
        limit = key.scope_id.rate_limit_per_hour or self.env["mcp.config"].get(
            "rate_limit_per_hour", 500)
        if not limit or not self.env["mcp.config"].get(
                "log_requests", True, cast=bool):
            return
        since = fields.Datetime.now() - timedelta(hours=1)
        used = self.env["mcp.audit.log"].sudo().search_count([
            ("api_key_id", "=", key.id),
            ("create_date", ">=", since),
        ])
        if used >= limit:
            raise AccessError(
                f"Rate limit reached for this key: {limit} calls/hour.")

    def _scope_line(self, key, model, op):
        line = key.scope_id.line_ids.filtered(lambda l: l.model_name == model)
        if not line or not getattr(line[0], f"can_{op}"):
            raise AccessError(f"MCP scope denies {op} on {model}")
        return line[0]

    def _tool_list_models(self, key, args):
        return [{"model": l.model_name,
                 "read": l.can_read, "create": l.can_create,
                 "write": l.can_write}
                for l in key.scope_id.line_ids]

    def _tool_search_records(self, key, args):
        model = args["model"]
        line = self._scope_line(key, model, "read")
        domain = ast.literal_eval(args.get("domain") or "[]")
        domain += ast.literal_eval(line.record_domain or "[]")
        blacklist = set((line.field_blacklist or "").replace(" ", "").split(","))
        fields_ = [f for f in (args.get("fields") or ["display_name"])
                   if f not in blacklist]
        # Settings > MCP Governance > Limits. Asking for more than the ceiling
        # is clamped, not refused - a hard error here just makes the model retry.
        Config = self.env["mcp.config"]
        default_limit = Config.get("default_record_limit", 10)
        max_limit = Config.get("max_record_limit", 100)
        limit = min(int(args.get("limit") or default_limit), max_limit)
        # env(user=key.user) already set by controller: ACLs + ir.rules apply.
        return self.env[model].search_read(domain, fields_, limit=limit)

    def _tool_create_record(self, key, args):
        model, values = args["model"], args["values"]
        line = self._scope_line(key, model, "create")
        self._strip_blacklist(line, values)
        if key.scope_id.require_approval:
            return self._queue_approval(key, "create", model, False, values)
        return {"id": self.env[model].create(values).id}

    def _tool_write_record(self, key, args):
        model, rid, values = args["model"], args["record_id"], args["values"]
        line = self._scope_line(key, model, "write")
        self._strip_blacklist(line, values)
        if key.scope_id.require_approval:
            return self._queue_approval(key, "write", model, rid, values)
        self.env[model].browse(rid).write(values)
        return {"id": rid, "written": True}

    def _strip_blacklist(self, line, values):
        for f in set((line.field_blacklist or "").replace(" ", "").split(",")):
            values.pop(f, None)

    def _queue_approval(self, key, op, model, rid, values):
        req = self.env["mcp.approval.request"].sudo().create({
            "api_key_id": key.id, "operation": op, "model_name": model,
            "record_id": rid or 0, "values_json": json.dumps(values, default=str),
        })
        return {"approval_required": True, "approval_id": req.id,
                "message": "Queued for human approval."}
