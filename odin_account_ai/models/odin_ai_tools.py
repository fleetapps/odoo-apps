"""The tools Ask the Ledger gives the model: read-only, run as the asking
user (access rights and record rules apply), capped in size.

Each result row carries an evidence handle ("E4") registered on the
conversation with what it stands for, so a handle cited in the answer opens
exactly the rows it came from: the P&L unfolded at that row, or the document.
The model never sees ids it could use to reach other data, and cannot write.

Tool definitions follow the Messages API tool format with ``strict: true``
(arguments are guaranteed to match the schema; bounds are checked here).
https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use
"""

import json

from odoo import _, api, fields, models
from odoo.fields import Domain
from odoo.tools import SQL

MAX_ROWS = 50
TEXT_LIMIT = 200

_DATE = {"type": "string", "format": "date"}
_NULL_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

TOOLS = [
    {
        "name": "balances",
        "description": (
            "Split one P&L line or one account into its contributors for a period: by partner, "
            "product, product_category, month, journal, or (for a line) account. Returns the ten "
            "largest and an 'Others' total, each with its evidence handle. Use comparison to get the "
            "previous period or the same period last year side by side."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["date_from", "date_to", "line_code", "account_code", "group_by", "comparison"],
            "properties": {
                "date_from": _DATE,
                "date_to": _DATE,
                "line_code": _NULL_STR,
                "account_code": _NULL_STR,
                "group_by": {"type": "string", "enum": [
                    "account", "partner", "product", "product_category", "month", "journal"]},
                "comparison": {"type": "string", "enum": ["none", "previous_period", "previous_year"]},
            },
        },
    },
    {
        "name": "entry",
        "description": "One journal entry (invoice, bill, payment or miscellaneous) by its number, with its lines.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["number"],
            "properties": {"number": {"type": "string"}},
        },
    },
    {
        "name": "find",
        "description": (
            "Look up accounts (by code or name), partners, journals or analytic accounts by name. "
            "Use it before filtering by a name the user typed."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "query"],
            "properties": {
                "kind": {"type": "string", "enum": ["account", "partner", "journal", "analytic_account"]},
                "query": {"type": "string"},
            },
        },
    },
    {
        "name": "journal_items",
        "description": (
            "Search posted journal items in a period, on any account (an account code prefix such as "
            "'6' works), optionally for one partner or containing a text in the label or reference, "
            "above an amount. Ordered by largest amount or most recent. At most 50."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["date_from", "date_to", "account_code", "line_code", "partner",
                         "text", "min_amount", "order", "limit"],
            "properties": {
                "date_from": _DATE,
                "date_to": _DATE,
                "account_code": _NULL_STR,
                "line_code": _NULL_STR,
                "partner": _NULL_STR,
                "text": _NULL_STR,
                "min_amount": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "order": {"type": "string", "enum": ["largest", "latest"]},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "pnl",
        "description": (
            "The Profit & Loss for a period: every line (Revenue, Gross Profit, ... Net Profit) or, "
            "with line_code, the accounts of one line. Optional comparison with the previous period "
            "or the same period last year, with growth in %."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["date_from", "date_to", "comparison", "line_code"],
            "properties": {
                "date_from": _DATE,
                "date_to": _DATE,
                "comparison": {"type": "string", "enum": ["none", "previous_period", "previous_year"]},
                "line_code": _NULL_STR,
            },
        },
    },
]


def tool_definitions():
    """In a fixed order, with strict schemas: the request prefix (and so the
    prompt cache) stays identical from one call to the next."""
    return [dict(tool, strict=True) for tool in sorted(TOOLS, key=lambda tool: tool["name"])]


class OdinAiTools(models.AbstractModel):
    _name = "odin.ai.tools"
    _description = "AI ledger tools"

    @api.model
    def run(self, conversation, name, args):
        """``(result, progress label)`` of one tool call. Errors come back as
        a result the model can read, not as an exception."""
        method = getattr(self, f"_tool_{name}", None)
        if not method or name not in {tool["name"] for tool in TOOLS}:
            return {"error": f"Unknown tool {name}."}, _("Thinking")
        try:
            return method(conversation, args or {})
        except Exception as error:  # noqa: BLE001 (any failure is reported to the model, which can adapt)
            return {"error": str(error)[:300]}, _("Reading the books")

    # ------------------------------------------------------------------
    # P&L
    # ------------------------------------------------------------------

    def _options(self, args):
        return {
            "date": {"filter": "custom", "date_from": args.get("date_from"), "date_to": args.get("date_to")},
            "comparison": {"mode": args.get("comparison") or "none", "periods": 1},
            "trend": False,
            "hide_zero": True,
        }

    def _tool_pnl(self, conversation, args):
        Report = self.env["odin.pnl.report"]
        options = self._options(args)
        if args.get("line_code"):
            report = Report.get_report(dict(options, unfolded=[f"L:{args['line_code']}"]))
            rows = [row for row in report["rows"] if row["parent"] == f"L:{args['line_code']}"]
        else:
            report = Report.get_report(options)
            rows = [row for row in report["rows"] if row["type"] != "heading"]
        columns = report["columns"]
        result_rows = []
        for row in rows[:MAX_ROWS]:
            handle = conversation._add_evidence({
                "kind": "pnl", "label": row["name"], "options": report["options"], "row_key": row["key"]})
            result_rows.append(self._pnl_row(row, columns, handle))
        label = _("Reading the P&L for %(period)s", period=columns[0]["label"])
        return {"columns": [column["label"] for column in columns], "rows": result_rows,
                "warnings": [w["message"] for w in report["warnings"]]}, label

    def _pnl_row(self, row, columns, handle):
        values = {column["label"]: _round(row["values"].get(column["key"])) for column in columns}
        return {
            "ref": handle,
            "code": row.get("code") or "",
            "name": _text(row["name"]),
            "values": values,
            "growth_pct": _round(row.get("growth")),
            "share_pct": _round(row.get("share")),
        }

    def _tool_balances(self, conversation, args):
        Report = self.env["odin.pnl.report"]
        options = self._options(args)
        group_by = args.get("group_by") or "partner"
        key = self._row_key(options, args.get("line_code"), args.get("account_code"))
        if not key:
            return {"error": "Give a line_code (such as OPEX) or an account_code that is on the P&L."}, \
                _("Reading the P&L")
        mode = "accounts" if group_by == "account" else group_by
        if mode == "accounts" and "/A:" in key:
            mode = "partner"
        children = Report.get_children(options, key, mode)["rows"]
        report = Report.get_report(dict(options, unfolded=[key.split("/")[0]]))
        columns = report["columns"]
        rows = []
        for row in children[:MAX_ROWS]:
            handle = conversation._add_evidence({
                "kind": "pnl", "label": row["name"], "options": dict(report["options"], unfolded=_parents(row["key"]),
                                                                       modes={key: mode}),
                "row_key": row["key"]})
            rows.append(self._pnl_row(row, columns, handle))
        return {"split_of": key, "by": group_by, "columns": [c["label"] for c in columns], "rows": rows}, \
            _("Splitting by %(dim)s", dim=group_by.replace("_", " "))

    def _row_key(self, options, line_code, account_code):
        Report = self.env["odin.pnl.report"]
        ctx = Report._context(options)
        if account_code:
            account = self.env["account.account"].with_context(active_test=False).search(
                [("code", "=", account_code), ("id", "in", ctx.pl_accounts.ids)], limit=1)
            if not account:
                return None
            for code, ids in ctx.line_accounts.items():
                if account.id in ids:
                    return f"L:{code}/A:{account.id}"
            return None
        if line_code and line_code in ctx.line_accounts:
            return f"L:{line_code}"
        return None

    # ------------------------------------------------------------------
    # Journal items
    # ------------------------------------------------------------------

    def _tool_journal_items(self, conversation, args):
        AML = self.env["account.move.line"]
        date_from = fields.Date.to_date(args.get("date_from"))
        date_to = fields.Date.to_date(args.get("date_to"))
        domain = Domain("parent_state", "=", "posted") & Domain("date", ">=", date_from) & \
            Domain("date", "<=", date_to) & Domain("company_id", "in", self.env.companies.ids) & \
            Domain("display_type", "not in", ("line_section", "line_subsection", "line_note"))
        if args.get("account_code"):
            domain &= Domain("account_id.code", "=like", f"{args['account_code']}%")
        if args.get("line_code"):
            ctx = self.env["odin.pnl.report"]._context(self._options(args))
            domain &= Domain("account_id", "in", ctx.line_accounts.get(args["line_code"], []))
        if args.get("partner"):
            domain &= Domain("partner_id", "ilike", args["partner"])
        if args.get("text"):
            domain &= Domain("name", "ilike", args["text"]) | Domain("move_id.ref", "ilike", args["text"])
        if args.get("min_amount"):
            amount = abs(float(args["min_amount"]))
            domain &= Domain("balance", ">=", amount) | Domain("balance", "<=", -amount)
        limit = min(max(int(args.get("limit") or 20), 1), MAX_ROWS)
        if args.get("order") == "latest":
            lines = AML.search(domain, order="date desc, id desc", limit=limit)
        else:
            query = AML._search(domain)
            query.order = SQL("ABS(%s) DESC", AML._field_to_sql(query.table, "balance", query))
            query.limit = limit
            lines = AML.browse([row[0] for row in self.env.execute_query(query.select())])
        rows = []
        for line in lines:
            handle = conversation._add_evidence({
                "kind": "record", "label": line.move_name or line.move_id.display_name,
                "model": "account.move", "res_id": line.move_id.id, "aml_id": line.id})
            rows.append({
                "ref": handle,
                "date": fields.Date.to_string(line.date),
                "entry": line.move_name,
                "account": line.account_id.display_name,
                "partner": _text(line.partner_id.display_name or ""),
                "label": _text(line.name or ""),
                "amount": _round(line.balance),
            })
        return {"items": rows, "note": "amount: debit positive, credit negative"}, _("Searching journal items")

    def _tool_entry(self, conversation, args):
        number = (args.get("number") or "").strip()
        move = self.env["account.move"].search([("name", "=", number)], limit=1)
        if not move:
            return {"error": f"No entry numbered {number}."}, _("Opening %(entry)s", entry=number)
        handle = conversation._add_evidence({
            "kind": "record", "label": move.name, "model": "account.move", "res_id": move.id})
        return {
            "ref": handle,
            "number": move.name,
            "type": dict(move._fields["move_type"]._description_selection(self.env)).get(move.move_type),
            "date": fields.Date.to_string(move.date),
            "partner": _text(move.partner_id.display_name or ""),
            "reference": _text(move.ref or ""),
            "state": move.state,
            "lines": [
                {"account": line.account_id.display_name, "label": _text(line.name or ""),
                 "debit": _round(line.debit), "credit": _round(line.credit)}
                for line in move.line_ids
                if line.display_type not in ("line_section", "line_subsection", "line_note")
            ],
        }, _("Opening %(entry)s", entry=move.name)

    def _tool_find(self, conversation, args):
        kind = args.get("kind")
        query = (args.get("query") or "").strip()
        model = {
            "account": "account.account",
            "partner": "res.partner",
            "journal": "account.journal",
            "analytic_account": "account.analytic.account",
        }.get(kind)
        if not model:
            return {"error": "kind must be account, partner, journal or analytic_account."}, _("Looking up")
        records = self.env[model].name_search(query, limit=10)
        found = []
        for record_id, name in records:
            entry = {"name": _text(name)}
            if model == "account.account":
                entry["code"] = self.env[model].browse(record_id).code
            found.append(entry)
        return {"matches": found}, _("Looking up “%(query)s”", query=query[:40])


def _round(value):
    return None if value is None else round(float(value), 2)


def _text(value):
    return (value or "")[:TEXT_LIMIT]


def _parents(key):
    parts = key.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)
