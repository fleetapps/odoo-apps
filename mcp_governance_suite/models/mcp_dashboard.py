# -*- coding: utf-8 -*-
"""Data behind the MCP Governance dashboard (the app's landing page).

One RPC, one payload. The client action is a plain OWL component with no
charting dependency, so everything it draws - series, bar lists, the setup
checklist - is computed here and shipped as plain JSON.

Aggregation goes through :meth:`_read_group` (Odoo 19 returns a list of
tuples), never a Python loop over the audit log: that table is the one that
grows without bound on a busy install.
"""
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

ADMIN_GROUP = "mcp_governance_suite.group_mcp_admin"

# How far back the activity chart looks.
TREND_DAYS = 14
# Rows shown in the "latest calls" table and in each bar list.
RECENT_LIMIT = 12
TOP_LIMIT = 6


class MCPDashboard(models.AbstractModel):
    _name = "mcp.dashboard"
    _description = "MCP Governance Dashboard"

    # ------------------------------------------------------------------ #
    #  Entry point
    # ------------------------------------------------------------------ #
    @api.model
    @api.readonly
    def get_dashboard_data(self):
        """Everything the dashboard renders, in one call.

        An abstract model has no ``ir.model.access`` line to lean on, so the
        group check is explicit - this method reads every key, scope and audit
        row on the database through ``sudo()``.
        """
        if not self.env.user.has_group(ADMIN_GROUP):
            raise AccessError(self.env._(
                "MCP Governance is restricted to its administrators."))

        Key = self.env["mcp.api.key"].sudo()
        Scope = self.env["mcp.scope"].sudo()
        Log = self.env["mcp.audit.log"].sudo()
        Approval = self.env["mcp.approval.request"].sudo()

        keys = Key.with_context(active_test=False).search([])
        scopes = Scope.search([])
        pending = Approval.search([("state", "=", "pending")], limit=50)

        return {
            "status": self._status(keys, scopes),
            "setup": self._setup_steps(keys, scopes),
            "kpis": self._kpis(keys, scopes, Log, Approval),
            "series": self._series(Log),
            "top_tools": self._top_tools(Log),
            "top_keys": self._top_keys(Log, keys),
            "recent": self._recent(Log),
            "approvals": self._approvals(pending),
            "scopes": self._scope_rows(scopes, keys),
            "keys": self._key_rows(keys),
        }

    # ------------------------------------------------------------------ #
    #  Header / status
    # ------------------------------------------------------------------ #
    def _status(self, keys, scopes):
        Config = self.env["mcp.config"]
        base_url = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "")
        return {
            "enabled": Config.get("enabled", False, cast=bool),
            "api_key_enabled": Config.get("api_key_enabled", True, cast=bool),
            "log_requests": Config.get("log_requests", True, cast=bool),
            "retention_days": Config.get("log_retention_days", 365),
            "default_limit": Config.get("default_record_limit", 10),
            "max_limit": Config.get("max_record_limit", 100),
            "rate_limit": Config.get("rate_limit_per_hour", 500),
            "endpoint": "%s/mcp/v1" % (base_url or "").rstrip("/"),
            "protocol": self._protocol_version(),
        }

    @staticmethod
    def _protocol_version():
        # Read from the controller so the badge can never drift from what the
        # endpoint actually answers in `initialize`.
        try:
            from ..controllers.mcp import PROTOCOL_VERSION
            return PROTOCOL_VERSION
        except Exception:  # noqa: BLE001 - a version badge must never 500
            return ""

    # ------------------------------------------------------------------ #
    #  Setup checklist
    # ------------------------------------------------------------------ #
    def _setup_steps(self, keys, scopes):
        """The order that actually works: scope, then key, then switch on.

        This is the whole onboarding of the module. A fresh install has three
        empty list views and a master switch, and nothing anywhere says which
        one to open first.
        """
        enabled = self.env["mcp.config"].get("enabled", False, cast=bool)
        usable = keys.filtered(lambda k: k.is_usable())
        scoped = scopes.filtered(lambda s: s.line_ids)
        return [
            {
                "key": "scope",
                "title": self.env._("Define a scope"),
                "detail": self.env._(
                    "An allow-list of models and operations. Anything not "
                    "listed is denied."),
                "done": bool(scoped),
                "action": "scopes",
                "cta": self.env._("New scope"),
            },
            {
                "key": "key",
                "title": self.env._("Issue an API key"),
                "detail": self.env._(
                    "Bound to one Odoo user and one scope. The secret is shown "
                    "once, at generation."),
                "done": bool(usable),
                "action": "keys",
                "cta": self.env._("New key"),
            },
            {
                "key": "enable",
                "title": self.env._("Switch the server on"),
                "detail": self.env._(
                    "Until then /mcp/v1 answers 503 to every caller, valid key "
                    "or not."),
                "done": enabled,
                "action": "settings",
                "cta": self.env._("Open settings"),
            },
        ]

    # ------------------------------------------------------------------ #
    #  KPIs
    # ------------------------------------------------------------------ #
    def _kpis(self, keys, scopes, Log, Approval):
        now = fields.Datetime.now()
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)
        prev_week = now - timedelta(days=14)

        calls_24h = Log.search_count([("create_date", ">=", day_ago)])
        calls_7d = Log.search_count([("create_date", ">=", week_ago)])
        prev_7d = Log.search_count([
            ("create_date", ">=", prev_week), ("create_date", "<", week_ago)])
        errors_7d = Log.search_count([
            ("create_date", ">=", week_ago), ("status", "=", "error")])

        tokens = Log._read_group(
            [("create_date", ">=", week_ago)], [], ["tokens_est:sum"])
        tokens_7d = (tokens[0][0] or 0) if tokens else 0

        active_keys = keys.filtered(lambda k: k.is_usable())
        expiring = keys.filtered(
            lambda k: k.expiry and 0 <= (k.expiry - fields.Date.today()).days <= 30
            and k.active)

        return {
            "keys_active": len(active_keys),
            "keys_total": len(keys),
            "keys_expiring": len(expiring),
            "scopes": len(scopes),
            "scopes_writable": len(scopes.filtered(lambda s: not s.read_only)),
            "calls_24h": calls_24h,
            "calls_7d": calls_7d,
            "calls_trend": self._trend(calls_7d, prev_7d),
            "errors_7d": errors_7d,
            "error_pct": round(100.0 * errors_7d / calls_7d, 1) if calls_7d else 0.0,
            "tokens_7d": tokens_7d,
            "pending_approvals": Approval.search_count([("state", "=", "pending")]),
        }

    @staticmethod
    def _trend(current, previous):
        """Percent change vs the previous window; ``None`` when there is no
        baseline, so the client can say 'no prior data' instead of '+100%'."""
        if not previous:
            return None
        return round(100.0 * (current - previous) / previous, 1)

    # ------------------------------------------------------------------ #
    #  Charts
    # ------------------------------------------------------------------ #
    def _series(self, Log):
        """Calls per day for the last :data:`TREND_DAYS`, split ok / error.

        Days with no traffic must still appear or the line lies about the
        shape of the activity, so the grouped rows are merged into a dense
        calendar rather than returned as-is.
        """
        today = fields.Date.context_today(self)
        start = today - timedelta(days=TREND_DAYS - 1)
        rows = Log._read_group(
            [("create_date", ">=", fields.Datetime.to_datetime(start))],
            ["create_date:day", "status"],
            ["__count"],
        )
        buckets = {}
        for day, status, count in rows:
            # `create_date:day` yields a date (or datetime) per group.
            key = fields.Date.to_date(day)
            slot = buckets.setdefault(key, {"ok": 0, "error": 0})
            slot["error" if status == "error" else "ok"] += count

        out = []
        for offset in range(TREND_DAYS):
            day = start + timedelta(days=offset)
            slot = buckets.get(day, {"ok": 0, "error": 0})
            out.append({
                "label": day.strftime("%d %b"),
                "short": day.strftime("%d"),
                "ok": slot["ok"],
                "error": slot["error"],
                "total": slot["ok"] + slot["error"],
            })
        return out

    def _top_tools(self, Log):
        week_ago = fields.Datetime.now() - timedelta(days=7)
        rows = Log._read_group(
            [("create_date", ">=", week_ago)], ["tool"], ["__count"],
            order="__count DESC", limit=TOP_LIMIT)
        return [{"label": tool or self.env._("(unnamed)"), "value": count}
                for tool, count in rows]

    def _top_keys(self, Log, keys):
        week_ago = fields.Datetime.now() - timedelta(days=7)
        rows = Log._read_group(
            [("create_date", ">=", week_ago), ("api_key_id", "!=", False)],
            ["api_key_id"], ["__count"], order="__count DESC", limit=TOP_LIMIT)
        return [{"label": key.name or self.env._("(deleted key)"), "value": count,
                 "id": key.id}
                for key, count in rows]

    # ------------------------------------------------------------------ #
    #  Tables
    # ------------------------------------------------------------------ #
    def _recent(self, Log):
        logs = Log.search([], limit=RECENT_LIMIT)
        return [{
            "id": log.id,
            "when": fields.Datetime.to_string(log.create_date),
            "user": log.user_id.display_name or "",
            "tool": log.tool or "",
            "status": log.status or "",
            "duration_ms": log.duration_ms,
            "tokens": log.tokens_est,
        } for log in logs]

    def _approvals(self, pending):
        return [{
            "id": req.id,
            "when": fields.Datetime.to_string(req.create_date),
            "operation": req.operation or "",
            "model_name": req.model_name or "",
            "record_id": req.record_id or 0,
            "key": req.api_key_id.name or "",
            "user": req.api_key_id.user_id.display_name or "",
        } for req in pending[:RECENT_LIMIT]]

    def _scope_rows(self, scopes, keys):
        by_scope = {}
        for key in keys:
            if key.scope_id:
                by_scope[key.scope_id.id] = by_scope.get(key.scope_id.id, 0) + 1
        return [{
            "id": scope.id,
            "name": scope.name or "",
            "read_only": scope.read_only,
            "require_approval": scope.require_approval,
            "models": len(scope.line_ids),
            "writable": len(scope.line_ids.filtered(
                lambda l: l.can_create or l.can_write or l.can_unlink)),
            "rate_limit": scope.rate_limit_per_hour,
            "keys": by_scope.get(scope.id, 0),
        } for scope in scopes]

    def _key_rows(self, keys):
        today = fields.Date.today()
        rows = []
        for key in keys:
            if key.expiry and key.expiry < today:
                state = "expired"
            elif not key.active:
                state = "archived"
            elif not key.key_hash:
                state = "no_secret"
            else:
                state = "active"
            rows.append({
                "id": key.id,
                "name": key.name or "",
                "user": key.user_id.display_name or "",
                "scope": key.scope_id.name or "",
                "preview": key.key_preview or "",
                "expiry": fields.Date.to_string(key.expiry) if key.expiry else "",
                "last_used": fields.Datetime.to_string(key.last_used) if key.last_used else "",
                "state": state,
            })
        return rows

    # ------------------------------------------------------------------ #
    #  Actions reachable from the dashboard
    # ------------------------------------------------------------------ #
    @api.model
    def approve(self, request_id):
        self._assert_approver()
        req = self.env["mcp.approval.request"].browse(request_id)
        req.action_approve()
        return {"state": req.state}

    @api.model
    def reject(self, request_id):
        self._assert_approver()
        self.env["mcp.approval.request"].browse(request_id).action_reject()
        return {"state": "rejected"}

    def _assert_approver(self):
        if not (self.env.user.has_group(ADMIN_GROUP)
                or self.env.user.has_group(
                    "mcp_governance_suite.group_mcp_approver")):
            raise AccessError(self.env._(
                "Only an MCP approver may act on approval requests."))
