"""The tools Ask the Ledger gives the model: read-only, run as the asking
user (access rights and record rules apply), capped in size.

Each result row carries an evidence handle ("E4") registered on the
conversation with what it stands for, so a handle cited in the answer opens
exactly the rows it came from: the P&L unfolded at that row, a partner's open
items, or the document. The model never sees ids it could use to reach other
data, and cannot write.

Tool definitions follow the Messages API tool format with ``strict: true``
(arguments are guaranteed to match the schema; bounds are checked here).
Structured outputs allow at most 16 parameters with union types across the
tools and the answer schema of one request (13 are used), so arguments added
later are plain strings where "" means "none", not nullable types.
https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use
https://platform.claude.com/docs/en/build-with-claude/structured-outputs

A conversation keeps the tool list it started with, so a tool keeps accepting
the arguments of its earlier versions: every argument is read with a default.
"""

import json
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.fields import Domain
from odoo.tools import SQL
from odoo.tools.misc import format_amount, format_date
from odoo.tools.sql import escape_psql

MAX_ROWS = 50
MAX_DOCUMENTS = 25
TOP_PARTNERS = 10
TEXT_LIMIT = 200

_DATE = {"type": "string", "format": "date"}
_NULL_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_COMPARISON = {"type": "string", "enum": ["none", "previous_period", "previous_year"]}


def _name(what):
    return {"type": "string", "description": f'The {what} to narrow to, by name; "" for all.'}


# Age of an open item: days past its due date, low and high bounds included.
BUCKETS = (
    ("not_due", None, None),
    ("days_1_30", 1, 30),
    ("days_31_60", 31, 60),
    ("days_61_90", 61, 90),
    ("over_90", 91, None),
)

# Journal items carry the commercial partner, so a partner named by the model
# resolves among companies and individuals, not their contacts.
PARTNER_DOMAIN = [("parent_id", "=", False)]
FILTERS = (
    ("journal", "account.journal", "journal_ids", None),
    ("partner", "res.partner", "partner_ids", PARTNER_DOMAIN),
    ("analytic_account", "account.analytic.account", "analytic_account_ids", None),
)

TOOLS = [
    {
        "name": "balances",
        "description": (
            "Split one P&L line or one account into its contributors for a period: by partner, "
            "product, product_category, month, journal, analytic (the analytic accounts of one plan, "
            "see plan) or, for a line, account. Returns the ten largest and an 'Others' total, each "
            "with its evidence handle. Use comparison to get the previous period or the same period "
            "last year side by side. journal, partner and analytic_account narrow the figures to one "
            "of each."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["date_from", "date_to", "line_code", "account_code", "group_by", "comparison",
                         "plan", "journal", "partner", "analytic_account"],
            "properties": {
                "date_from": _DATE,
                "date_to": _DATE,
                "line_code": _NULL_STR,
                "account_code": _NULL_STR,
                "group_by": {"type": "string", "enum": [
                    "account", "partner", "product", "product_category", "month", "journal", "analytic"]},
                "comparison": _COMPARISON,
                "plan": {"type": "string",
                         "description": 'The analytic plan to split by when group_by is analytic; "" for the first.'},
                "journal": _name("journal"),
                "partner": _name("partner"),
                "analytic_account": _name("analytic account"),
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
            "Look up accounts (by code or name), partners, journals, analytic accounts, analytic plans "
            "or P&L budgets by name. It returns the names (and codes) the other tools accept: use it "
            "before filtering by a name the user typed."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "query"],
            "properties": {
                "kind": {"type": "string", "enum": [
                    "account", "partner", "journal", "analytic_account", "analytic_plan", "budget"]},
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
        "name": "open_items",
        "description": (
            "Unpaid customer invoices (receivable) or supplier bills (payable) open today, aged by due "
            "date: not due, 1-30, 31-60, 61-90 and over 90 days overdue. group_by partner gives the ten "
            "largest balances with their ageing and an 'Others' total; bucket gives the total of each "
            "age; document lists the open documents, most overdue first (at most 25). Amounts are "
            "positive: what customers owe us, or what we owe suppliers."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "group_by", "partner"],
            "properties": {
                "kind": {"type": "string", "enum": ["receivable", "payable"]},
                "group_by": {"type": "string", "enum": ["partner", "bucket", "document"]},
                "partner": _name("partner"),
            },
        },
    },
    {
        "name": "pnl",
        "description": (
            "The Profit & Loss for a period: every line (Revenue, Gross Profit, ... Net Profit) or, "
            "with line_code, the accounts of one line. Optional comparison with the previous period "
            "or the same period last year, with growth in %. budget adds per row the budget, the % "
            "achieved and the variance (actual minus budget; on a cost line a positive variance is "
            'overspending): "auto" for the company budget covering the period, a budget name, or "" '
            "for none. journal, partner and analytic_account narrow the figures to one of each."),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["date_from", "date_to", "comparison", "line_code", "budget",
                         "journal", "partner", "analytic_account"],
            "properties": {
                "date_from": _DATE,
                "date_to": _DATE,
                "comparison": _COMPARISON,
                "line_code": _NULL_STR,
                "budget": {"type": "string",
                           "description": '"auto", a budget name, or "" for no budget.'},
                "journal": _name("journal"),
                "partner": _name("partner"),
                "analytic_account": _name("analytic account"),
            },
        },
    },
]


def tool_definitions():
    """In a fixed order, with strict schemas: the request prefix (and so the
    prompt cache) stays identical from one call to the next."""
    return [dict(tool, strict=True) for tool in sorted(TOOLS, key=lambda tool: tool["name"])]


# The tools as conversations started before the tool list was stored with
# them received them, byte for byte: such a conversation keeps getting these
# (a changed tool list would invalidate its model's earlier reasoning). Their
# calls are served by the same methods, which default the arguments added since.
TOOLS_V1 = [
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


def legacy_tool_definitions():
    return [dict(tool, strict=True) for tool in sorted(TOOLS_V1, key=lambda tool: tool["name"])]


class ToolError(Exception):
    """A request the tool cannot serve as asked (an unknown or ambiguous
    name): the message goes back to the model, which can adapt."""


class OdinAiTools(models.AbstractModel):
    _name = "odin.ai.tools"
    _description = "AI ledger tools"

    @api.model
    def run(self, conversation, name, args):
        """``(result, progress label)`` of one tool call. Errors come back as
        a result the model can read, not as an exception. A result may carry
        ``_scope`` (what was read, for the answer's "Based on" line), which
        the conversation takes out before the result goes to the model."""
        method = getattr(self, f"_tool_{name}", None)
        if not method or name not in {tool["name"] for tool in TOOLS}:
            return {"error": f"Unknown tool {name}."}, _("Thinking")
        try:
            return method(conversation, args or {})
        except ToolError as error:
            return {"error": str(error)}, _("Looking up")
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
        filters, filter_names = self._filter_options(args)
        options = dict(self._options(args), **filters)
        budget = self._budget(args.get("budget"), args.get("date_from"), args.get("date_to"))
        if budget:
            options["budget_id"] = budget.id
        code = (args.get("line_code") or "").strip()
        if code:
            report = Report.get_report(dict(options, unfolded=[f"L:{code}"]))
            rows = [row for row in report["rows"] if row["parent"] == f"L:{code}"]
            if not rows:
                error = self._line_code_error(report, code)
                if error:
                    return {"error": error}, _("Reading the P&L")
        else:
            report = Report.get_report(options)
            rows = [row for row in report["rows"] if row["type"] != "heading"]
        columns = report["columns"]
        period = columns[0]["label"]
        result_rows = []
        for row in rows[:MAX_ROWS]:
            handle = conversation._add_evidence({
                "kind": "pnl", "label": f"{row['name']} · {period}", "options": report["options"],
                "row_key": row["key"]})
            result_rows.append(self._pnl_row(row, columns, handle))
        result = {"columns": [column["label"] for column in columns], "rows": result_rows,
                  "warnings": [w["message"] for w in report["warnings"]],
                  "_scope": self._pnl_scope(report, filter_names, budget)}
        if budget:
            result["budget"] = budget.name
        return result, _("Reading the P&L for %(period)s", period=period)

    def _line_code_error(self, report, code):
        """Why ``code`` gave no accounts, or None when it is an accounts line
        with nothing posted (an empty list is then the true answer)."""
        lines = self.env["odin.pnl.layout"].browse(report["options"]["layout_id"]).line_ids
        line = lines.filtered(lambda candidate: candidate.code == code)[:1]
        if not line:
            return f"Unknown line code {code}. The lines are: {', '.join(lines.sorted('sequence').mapped('code'))}."
        if line.kind == "formula":
            return (f"{code} is a total computed from other lines: read pnl without line_code, or the "
                    "lines it is built from.")
        if line.kind == "heading":
            return f"{code} is a heading: read the lines under it."
        return None

    def _pnl_row(self, row, columns, handle):
        values = {column["label"]: _round(row["values"].get(column["key"])) for column in columns}
        entry = {
            "ref": handle,
            "code": row.get("code") or "",
            "name": _text(row["name"]),
            "values": values,
            "growth_pct": _round(row.get("growth")),
            "share_pct": _round(row.get("share")),
        }
        budget_columns = [column for column in columns if column.get("budget")]
        if budget_columns:
            # Variance computed here, so the model never subtracts.
            entry["budget"], entry["budget_pct"], entry["variance"] = {}, {}, {}
            for column in budget_columns:
                actual = row["values"].get(column["key"])
                planned = row["budget"].get(column["key"])
                entry["budget"][column["label"]] = _round(planned)
                entry["budget_pct"][column["label"]] = _round(row["budget_pct"].get(column["key"]))
                entry["variance"][column["label"]] = (
                    _round(actual - planned) if actual is not None and planned is not None else None)
        return entry

    def _tool_balances(self, conversation, args):
        Report = self.env["odin.pnl.report"]
        filters, filter_names = self._filter_options(args)
        options = dict(self._options(args), **filters)
        group_by = args.get("group_by") or "partner"
        key = self._row_key(options, args.get("line_code"), args.get("account_code"))
        if not key:
            return {"error": "Give a line_code (such as OPEX) or an account_code that is on the P&L."}, \
                _("Reading the P&L")
        mode = "accounts" if group_by == "account" else group_by
        if mode == "accounts" and "/A:" in key:
            mode = "partner"
        if mode == "analytic":
            mode = f"analytic:{self._plan(args.get('plan')).id}"
        children = Report.get_children(options, key, mode)["rows"]
        report = Report.get_report(dict(options, unfolded=[key.split("/")[0]]))
        columns = report["columns"]
        period = columns[0]["label"]
        parent = next((row["name"] for row in report["rows"] if row["key"] == key), "")
        rows = []
        for row in children[:MAX_ROWS]:
            handle = conversation._add_evidence({
                "kind": "pnl", "label": " · ".join(filter(None, (row["name"], parent, period))),
                "options": dict(report["options"], unfolded=_parents(row["key"]), modes={key: mode}),
                "row_key": row["key"]})
            rows.append(self._pnl_row(row, columns, handle))
        return {"split_of": key, "by": group_by, "columns": [c["label"] for c in columns], "rows": rows,
                "_scope": self._pnl_scope(report, filter_names)}, \
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

    def _pnl_scope(self, report, filter_names=(), budget=None):
        """The period, comparison and filters a P&L read covered, as one
        "Based on" item opening the P&L on them."""
        columns = report["columns"]
        current = ", ".join(c["label"] for c in columns if c.get("group") != "comparison")
        compared = ", ".join(c["label"] for c in columns if c.get("group") == "comparison")
        label = _("%(period)s vs %(comparison)s", period=current, comparison=compared) if compared else current
        details = list(filter_names) + ([_("budget %(name)s", name=budget.name)] if budget else [])
        if details:
            label = f"{label} ({', '.join(details)})"
        options = dict(report["options"], unfolded=[], modes={})
        key = json.dumps({name: options.get(name) for name in (
            "date", "comparison", "journal_ids", "partner_ids", "analytic_account_ids", "budget_id")},
            sort_keys=True, default=str)
        return {"key": key, "label": label,
                "evidence": {"kind": "pnl", "label": label, "options": options, "row_key": ""}}

    # ------------------------------------------------------------------
    # Names given by the model
    # ------------------------------------------------------------------

    def _filter_options(self, args):
        """Engine options for the journal, partner and analytic account named
        in ``args`` ("" or absent for none), with their names."""
        options, names = {}, []
        for arg, model, key, domain in FILTERS:
            name = (args.get(arg) or "").strip()
            if name:
                record = self._resolve(model, name, arg.replace("_", " "), domain)
                options[key] = [record.id]
                names.append(record.display_name)
        return options, names

    def _resolve(self, model, name, what, domain=None):
        """One record by name: the only one named exactly so (case aside), by
        any field the model is searched by (its name, a code), else the only
        one whose name contains it. The exact search comes first, so partial
        matches cannot crowd it out of the candidates."""
        Model = self.env[model]
        records = Model
        for operator, value, limit in (("=ilike", escape_psql(name), 2), ("ilike", name, 6)):
            records = Model.browse([record_id for record_id, _display in Model.name_search(
                value, domain=domain or [], operator=operator, limit=limit)])
            if records:
                break
        if not records:
            raise ToolError(f"No {what} matches {name!r}.")
        if len(records) == 1:
            return records
        listed = "; ".join(_plain_name(record) for record in records)
        hint = " or give its code" if "code" in Model._fields else ""
        raise ToolError(f"Several {what}s match {name!r}: {listed}. Name one exactly{hint}.")

    def _plan(self, name):
        name = (name or "").strip()
        Plan = self.env["account.analytic.plan"]
        if name:
            return self._resolve("account.analytic.plan", name, "analytic plan", [("parent_id", "=", False)])
        plan = Plan.search([("parent_id", "=", False)], limit=1)
        if not plan:
            raise ToolError("There is no analytic plan.")
        return plan

    def _budget(self, name, date_from, date_to):
        """The P&L budget named, the one covering most of the period for
        "auto", or nothing for ""."""
        Budget = self.env["odin.pnl.budget"]
        name = (name or "").strip()
        if not name:
            return Budget
        domain = [("company_id", "in", self.env.companies.ids)]
        if name.lower() != "auto":
            return self._resolve("odin.pnl.budget", name, "budget", domain)
        start, end = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
        budgets = Budget.search(domain + [("date_from", "<=", end), ("date_to", ">=", start)])
        if not budgets:
            raise ToolError("No budget covers this period.")
        return max(budgets, key=lambda budget: (
            (min(budget.date_to, end) - max(budget.date_from, start)).days, budget.date_from))

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
            number = line.move_name or line.move_id.display_name
            handle = conversation._add_evidence({
                "kind": "record",
                "label": " · ".join((number, self._date(line.date), self._amount(line.balance))),
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
        report_options = self.env["odin.pnl.report"]._normalize_options(dict(self._options(args), comparison={}))
        label = _("%(start)s to %(end)s", start=self._date(date_from), end=self._date(date_to))
        scope = {"key": json.dumps(report_options["date"], sort_keys=True), "label": label,
                 "evidence": {"kind": "pnl", "label": label, "options": report_options, "row_key": ""}}
        return {"items": rows, "note": "amount: debit positive, credit negative", "_scope": scope}, \
            _("Searching journal items")

    def _tool_entry(self, conversation, args):
        number = (args.get("number") or "").strip()
        moves = self.env["account.move"].search([("name", "=", number)], limit=10)
        if not moves:
            return {"error": f"No entry numbered {number}."}, _("Opening %(entry)s", entry=number)
        # The same number can exist in each selected company: the current
        # company's comes first, the others are named.
        move = moves.filtered(lambda candidate: candidate.company_id == self.env.company)[:1] or moves[:1]
        handle = conversation._add_evidence({
            "kind": "record", "label": " · ".join((move.name, self._date(move.date))),
            "model": "account.move", "res_id": move.id})
        result = {
            "ref": handle,
            "number": move.name,
            "company": move.company_id.name,
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
        }
        if moves - move:
            result["same_number_in"] = (moves - move).mapped("company_id.name")
        return result, _("Opening %(entry)s", entry=move.name)

    def _tool_find(self, conversation, args):
        kind = args.get("kind")
        query = (args.get("query") or "").strip()
        label = _("Looking up “%(query)s”", query=query[:40])
        if kind == "budget":
            budgets = self.env["odin.pnl.budget"].search(
                [("company_id", "in", self.env.companies.ids), ("name", "ilike", query)], limit=10)
            return {"matches": [
                {"name": _text(budget.name), "from": fields.Date.to_string(budget.date_from),
                 "to": fields.Date.to_string(budget.date_to)} for budget in budgets]}, label
        model = {
            "account": "account.account",
            "partner": "res.partner",
            "journal": "account.journal",
            "analytic_account": "account.analytic.account",
            "analytic_plan": "account.analytic.plan",
        }.get(kind)
        if not model:
            return {"error": "kind must be account, partner, journal, analytic_account, analytic_plan or budget."}, \
                _("Looking up")
        # Partners as the books carry them (no contacts), root plans only: the
        # names the other tools resolve.
        domain = {"partner": PARTNER_DOMAIN, "analytic_plan": [("parent_id", "=", False)]}.get(kind, [])
        Model = self.env[model]
        records = Model.browse([record_id for record_id, _display in Model.name_search(query, domain=domain, limit=10)])
        found = []
        for record in records:
            # The plain name, not the display name, which can add a code in
            # brackets, a currency or a partner that a search by name misses.
            entry = {"name": _text(record[record._rec_name] or "")}
            if "code" in record._fields and record.code:
                entry["code"] = record.code
            if kind == "analytic_account":
                entry["plan"] = record.plan_id.name
            found.append(entry)
        return {"matches": found}, label

    # ------------------------------------------------------------------
    # Receivables and payables
    # ------------------------------------------------------------------

    def _tool_open_items(self, conversation, args):
        kind = "payable" if args.get("kind") == "payable" else "receivable"
        group_by = args.get("group_by") if args.get("group_by") in ("partner", "bucket", "document") else "partner"
        today = fields.Date.context_today(self)
        # amount_residual is signed like a balance: receivables are debits,
        # payables credits. Both are returned positive.
        sign = -1 if kind == "payable" else 1
        domain = (
            Domain("parent_state", "=", "posted")
            & Domain("account_id.account_type", "=", "asset_receivable" if kind == "receivable" else "liability_payable")
            & Domain("reconciled", "=", False)
            & Domain("amount_residual", "!=", 0)
            & Domain("company_id", "in", self.env.companies.ids)
        )
        partner_name = (args.get("partner") or "").strip()
        if partner_name:
            partner = self._resolve("res.partner", partner_name, "partner", PARTNER_DOMAIN)
            domain &= Domain("partner_id", "child_of", partner.id)
        what = _("receivables") if kind == "receivable" else _("payables")
        on_date = self._date(today)
        scope_label = _("%(what)s open on %(date)s", what=what.capitalize(), date=on_date)
        scope = {"key": f"open:{kind}:{today}", "label": scope_label}
        if group_by == "document":
            result = self._open_documents(conversation, domain, today, sign)
        else:
            result = self._open_by_age(conversation, domain, today, sign, group_by, what, on_date)
        result["_scope"] = scope
        result["as_of"] = fields.Date.to_string(today)
        return result, _("Ageing %(what)s", what=what)

    def _bucket_domain(self, today, low, high):
        if low is None:
            return Domain("date_maturity", "=", False) | Domain("date_maturity", ">=", today)
        domain = Domain("date_maturity", "<=", today - timedelta(days=low))
        if high is not None:
            domain &= Domain("date_maturity", ">=", today - timedelta(days=high))
        return domain

    def _open_by_age(self, conversation, domain, today, sign, group_by, what, on_date):
        AML = self.env["account.move.line"]
        per_partner = {}
        totals = {}
        for bucket, low, high in BUCKETS:
            bucket_domain = domain & self._bucket_domain(today, low, high)
            if group_by == "partner":
                for partner, amount in AML._read_group(bucket_domain, ["partner_id"], ["amount_residual:sum"]):
                    per_partner.setdefault(partner, {})[bucket] = sign * (amount or 0.0)
            else:
                [(amount,)] = AML._read_group(bucket_domain, [], ["amount_residual:sum"])
                totals[bucket] = sign * (amount or 0.0)
        if group_by == "bucket":
            return {"buckets": {bucket: _round(totals[bucket]) for bucket, _low, _high in BUCKETS},
                    "total": _round(sum(totals.values()))}
        ranked = sorted(per_partner.items(), key=lambda item: -sum(item[1].values()))
        rows = []
        for partner, buckets in ranked[:TOP_PARTNERS]:
            name = partner.display_name if partner else _("No partner")
            handle = conversation._add_evidence({
                "kind": "list", "model": "account.move.line",
                "label": " · ".join((name, _("%(what)s open on %(date)s", what=what, date=on_date))),
                "title": _("%(partner)s: open %(what)s", partner=name, what=what),
                "domain": list(domain & Domain("partner_id", "=", partner.id or False))})
            rows.append({"ref": handle, "partner": _text(name), "total": _round(sum(buckets.values())),
                         **{bucket: _round(buckets.get(bucket, 0.0)) for bucket, _low, _high in BUCKETS}})
        others = ranked[TOP_PARTNERS:]
        if others:
            rows.append({"partner": f"Others ({len(others)} partners)",
                         "total": _round(sum(sum(buckets.values()) for _partner, buckets in others)),
                         **{bucket: _round(sum(buckets.get(bucket, 0.0) for _partner, buckets in others))
                            for bucket, _low, _high in BUCKETS}})
        return {"rows": rows, "total": _round(sum(sum(b.values()) for b in per_partner.values()))}

    def _open_documents(self, conversation, domain, today, sign):
        AML = self.env["account.move.line"]
        groups = AML._read_group(domain, ["move_id"], ["amount_residual:sum", "date_maturity:min"])
        documents = sorted(
            ((max((today - due).days, 0) if due else 0, sign * (amount or 0.0), move, due)
             for move, amount, due in groups),
            key=lambda item: (-item[0], -item[1]))
        rows = []
        for days, amount, move, due in documents[:MAX_DOCUMENTS]:
            handle = conversation._add_evidence({
                "kind": "record", "model": "account.move", "res_id": move.id,
                "label": " · ".join(filter(None, (
                    move.name, _("due %(date)s", date=self._date(due)) if due else "", self._amount(amount))))})
            rows.append({"ref": handle, "number": move.name, "partner": _text(move.partner_id.display_name or ""),
                         "due": fields.Date.to_string(due) if due else None, "days_overdue": days,
                         "amount": _round(amount)})
        return {"documents": rows, "count": len(documents), "total": _round(sum(doc[1] for doc in documents))}

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _date(self, value):
        return format_date(self.env, value, date_format="d MMM y") if value else ""

    def _amount(self, value):
        return format_amount(self.env, value or 0.0, self.env.company.currency_id)


def _round(value):
    return None if value is None else round(float(value), 2)


def _text(value):
    return (value or "")[:TEXT_LIMIT]


def _parents(key):
    parts = key.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def _plain_name(record):
    """A record as the tools take it back: its name, and its code if any."""
    name = record[record._rec_name] or ""
    code = "code" in record._fields and record.code
    return f"{name} (code {code})" if code else name


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)
