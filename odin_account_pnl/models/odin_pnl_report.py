"""The engine of the interactive Profit & Loss.

One page, as in Odoo 19 Enterprise: the report, its unfolded rows and the
figures behind any cell all come from this model, called by the client action
through ``orm.call``. Reference for the behaviour cloned:
https://www.odoo.com/documentation/19.0/applications/finance/accounting/reporting.html

How the figures are computed
----------------------------
* Journal items are read through ``account.move.line._search(domain)``, which
  applies the user's access rights and record rules (odoo/orm/models.py), and
  aggregated in a single SQL statement built with ``odoo.tools.SQL`` and
  ``_field_to_sql`` so the fields read are flushed first
  (https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html#sql-execution).
* All the period columns come from the same statement, one ``SUM(CASE ...)``
  per column, so overlapping columns (months and their total) cost nothing.
* P&L accounts are found first by type (``account_type`` is not stored on
  journal items), archived ones included: an archived account with postings
  still belongs in the P&L.
* An analytic filter prorates amounts by distribution percentage: a bill split
  60/40 between two departments counts 60% in the first. Keys of
  ``analytic_distribution`` may combine plans ("12,15"); a key counts when it
  contains a selected account.

Rows and keys
-------------
Every row has a key: a path of segments separated by ``/``, read from the top
line down, for example ``L:REV/A:42/B:partner:7``. ``L`` is a layout line,
``G`` an account group, ``A`` an account, ``B`` a breakdown value ("What's in
this number"), ``M`` a journal item. The path is all the server needs to know
which journal items a row stands for, so unfolding, exporting and opening the
items behind a row all use the same code.
"""

import json
from collections import defaultdict
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import SQL, date_utils, format_date, html2plaintext

from . import odin_pnl_formula, odin_pnl_periods as periods
from .odin_pnl_layout import PL_ACCOUNT_TYPES

PAGE_SIZE = 80
TOP_CONTRIBUTORS = 10
MAX_UNFOLDED = 400
TREND_MONTHS = 12
UNALLOCATED = "_UNALLOCATED"

DIMENSIONS = ("partner", "product", "product_category", "month", "journal", "analytic")
# What unfolding a row shows when the user has not picked anything.
ENTRY_MODES = ("entries", "ledger")


class OdinPnlReport(models.AbstractModel):
    _name = "odin.pnl.report"
    _description = "Interactive Profit & Loss"

    # ------------------------------------------------------------------
    # Entry points (called by the client action)
    # ------------------------------------------------------------------

    @api.model
    def get_report(self, options=None):
        """The whole report for ``options``, with the rows listed in
        ``options["unfolded"]`` already unfolded (so a page restored from the
        breadcrumb, or exported, shows exactly what was on screen)."""
        self._check_access()
        ctx = self._context(options)
        rows = self._top_rows(ctx)
        unfolded = set(ctx.options["unfolded"])
        if ctx.options["unfold_all"]:
            unfolded |= {row["key"] for row in rows if row["unfoldable"]}
        result = []
        for row in rows:
            result.append(row)
            if row["key"] in unfolded and row["unfoldable"]:
                row["unfolded"] = True
                result.extend(self._unfold(ctx, row, unfolded))
        return {
            "options": ctx.options,
            "columns": ctx.public_columns(),
            "rows": result,
            "warnings": ctx.warnings,
            "currency": {
                "symbol": ctx.currency.symbol,
                "position": ctx.currency.position,
                "decimals": ctx.currency.decimal_places,
            },
            "layouts": [
                {"id": layout.id, "name": layout.name}
                for layout in self._layouts()
            ],
            "budgets": [
                {"id": budget.id, "name": budget.name}
                for budget in self.env["odin.pnl.budget"].search(
                    [("company_id", "in", ctx.companies.ids)])
            ],
            "can_edit_budget": self.env.user.has_group("account.group_account_user"),
            "can_configure": self.env.user.has_group("account.group_account_manager"),
            "analytic_plans": [
                {"id": plan.id, "name": plan.name}
                for plan in self.env["account.analytic.plan"].search([("parent_id", "=", False)])
            ],
        }

    @api.model
    def get_children(self, options, row_key, mode=None, offset=0, running=0.0, show_all=False):
        """The rows under ``row_key`` when it is unfolded with ``mode``:
        ``accounts`` (for a layout line), ``entries``, ``ledger``,
        ``audit:<column key>``, or a breakdown dimension such as
        ``partner`` or ``analytic:<plan id>``. Entries come by pages of
        ``PAGE_SIZE`` from ``offset``; ``running`` carries the ledger's
        running balance from the previous page. ``show_all`` lists every
        contributor of a breakdown instead of the top ones and "Others"."""
        self._check_access()
        ctx = self._context(options)
        parent = self._row_for_key(ctx, row_key)
        parent["show_all"] = bool(show_all)
        if mode:
            parent["mode"] = self._valid_mode(ctx, parent, mode)
        return {
            "rows": self._children(ctx, parent, offset=int(offset or 0), running=float(running or 0.0)),
            "mode": parent["mode"],
        }

    @api.model
    def action_open_items(self, options, row_key, column_key=None):
        """The standard Journal Items list behind a row (and one column), as
        Enterprise's "Journal Items" link, for bulk edits such as analytic
        distribution en masse."""
        self._check_access()
        ctx = self._context(options)
        filt = self._path_filter(ctx, self._parse_key(row_key))
        date_from, date_to = ctx.date_min, ctx.date_max
        if column_key:
            column = ctx.column(column_key)
            date_from, date_to = column["date_from"], column["date_to"]
        domain = (
            ctx.base_domain
            & Domain("account_id", "in", filt["account_ids"])
            & Domain("date", ">=", date_from)
            & Domain("date", "<=", date_to)
            & filt["domain"]
            & self._analytic_domain(filt["sets"])
        )
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_account_moves_all_a")
        action.update({
            "name": _("Journal Items"),
            "domain": list(domain),
            "context": {"create": False, "search_default_group_by_account": 0},
            "target": "current",
        })
        return action

    @api.model
    def action_open_move(self, move_line_id):
        """The source document of a journal item (invoice, bill, payment or
        entry: all are ``account.move`` forms)."""
        self._check_access()
        line = self.env["account.move.line"].browse(int(move_line_id)).exists()
        if not line:
            raise UserError(_("This journal item no longer exists."))
        line.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": line.move_id.id,
            "views": [(False, "form")],
            "target": "current",
        }

    @api.model
    def get_entry_peek(self, move_line_id):
        """The source document of a journal item, for the side panel: what
        an accountant checks without leaving the report."""
        self._check_access()
        line = self.env["account.move.line"].browse(int(move_line_id)).exists()
        if not line:
            raise UserError(_("This journal item no longer exists."))
        line.check_access("read")
        move = line.move_id
        selection = dict(move._fields["move_type"]._description_selection(self.env))
        states = dict(move._fields["state"]._description_selection(self.env))
        payment_states = dict(move._fields["payment_state"]._description_selection(self.env))
        attachments = self.env["ir.attachment"].search([
            ("res_model", "=", "account.move"), ("res_id", "=", move.id),
        ], order="id desc", limit=10)
        main = move.message_main_attachment_id if "message_main_attachment_id" in move._fields else False
        if main and main not in attachments:
            attachments = main | attachments
        messages = move.message_ids.filtered(
            lambda message: message.message_type in ("comment", "email") and message.body)[:3]
        analytic = self.env["account.analytic.account"]
        lines = []
        for item in move.line_ids.filtered(lambda item: item.display_type not in ("line_section", "line_subsection", "line_note")):
            distribution = item.analytic_distribution or {}
            ids = {int(i) for key in distribution for i in key.split(",")}
            names = {acc.id: acc.display_name for acc in analytic.browse(list(ids)).exists()}
            lines.append({
                "id": item.id,
                "account": item.account_id.display_name,
                "label": item.name or "",
                "partner": item.partner_id.display_name or "",
                "debit": item.debit,
                "credit": item.credit,
                "analytic": [
                    {"name": " / ".join(names.get(int(i), "?") for i in key.split(",")), "percent": pct}
                    for key, pct in distribution.items()
                ],
                "current": item == line,
            })
        return {
            "move_id": move.id,
            "name": move.name or _("Draft"),
            "type": selection.get(move.move_type, ""),
            "state": move.state,
            "state_label": states.get(move.state, ""),
            "payment_state": move.payment_state if move.is_invoice(include_receipts=True) else False,
            "payment_state_label": payment_states.get(move.payment_state, "")
            if move.is_invoice(include_receipts=True) else "",
            "date": fields.Date.to_string(move.date),
            "invoice_date": fields.Date.to_string(move.invoice_date) if move.invoice_date else False,
            "due_date": fields.Date.to_string(move.invoice_date_due) if move.invoice_date_due else False,
            "partner": move.partner_id.display_name or "",
            "ref": move.ref or "",
            "journal": move.journal_id.display_name,
            "amount_total": move.amount_total if move.is_invoice(include_receipts=True) else False,
            "currency": move.currency_id.symbol or move.currency_id.name,
            "lines": lines,
            "attachments": [
                {"id": attachment.id, "name": attachment.name, "mimetype": attachment.mimetype or ""}
                for attachment in attachments
            ],
            "messages": [
                {
                    "author": message.author_id.display_name or "",
                    "date": fields.Datetime.to_string(message.date),
                    "body": html2plaintext(message.body or "")[:500],
                }
                for message in messages
            ],
        }

    # ------------------------------------------------------------------
    # Annotations and budgets, edited from the page
    # ------------------------------------------------------------------

    @api.model
    def get_annotations(self, options, row_key):
        self._check_access()
        ctx = self._context(options)
        code, account_id = self._annotation_target(row_key)
        notes = self.env["odin.pnl.annotation"].search([
            ("company_id", "in", ctx.companies.ids),
            ("layout_id", "in", [False, ctx.layout.id]),
            ("line_code", "=", code),
            ("account_id", "=", account_id),
        ])
        return [
            {
                "id": note.id,
                "note": note.note,
                "date": fields.Date.to_string(note.date) if note.date else False,
                "author": note.author_id.display_name,
                "can_edit": note.author_id == self.env.user
                or self.env.user.has_group("account.group_account_manager"),
            }
            for note in notes
        ]

    @api.model
    def add_annotation(self, options, row_key, note, dated=True):
        self._check_access()
        self._check_accountant()
        ctx = self._context(options)
        code, account_id = self._annotation_target(row_key)
        if not (note or "").strip():
            raise UserError(_("Write something first."))
        self.env["odin.pnl.annotation"].create({
            "company_id": self.env.company.id,
            "layout_id": ctx.layout.id,
            "line_code": code,
            "account_id": account_id,
            "date": ctx.balance_columns[0]["date_to"] if dated else False,
            "note": note.strip(),
        })
        return self.get_annotations(options, row_key)

    @api.model
    def delete_annotation(self, annotation_id):
        self._check_access()
        self._check_accountant()
        self.env["odin.pnl.annotation"].browse(int(annotation_id)).exists().unlink()
        return True

    def _annotation_target(self, row_key):
        segments = self._parse_key(row_key)
        if not segments or segments[0][0] != "L":
            raise UserError(_("Notes go on a line or an account."))
        if len(segments) == 1:
            return segments[0][1], False
        if segments[-1][0] == "A" and len(segments) <= 3:
            return segments[0][1], _int(segments[-1][1]) or False
        raise UserError(_("Notes go on a line or an account."))

    @api.model
    def create_budget(self, options, name):
        """A new budget covering the fiscal year of the period on screen."""
        self._check_access()
        self._check_accountant()
        ctx = self._context(options)
        if not (name or "").strip():
            raise UserError(_("Give the budget a name."))
        year = ctx.fiscal(ctx.balance_columns[0]["date_from"])
        budget = self.env["odin.pnl.budget"].create({
            "name": name.strip(),
            "company_id": self.env.company.id,
            "date_from": year[0],
            "date_to": year[1],
        })
        return budget.id

    @api.model
    def set_budget_amount(self, options, row_key, column_key, amount):
        """Type a budget in an account's cell. ``amount`` is as displayed
        (income positive); a column of several months is spread evenly,
        the rounding difference on the last month."""
        self._check_access()
        self._check_accountant()
        ctx = self._context(options)
        if not ctx.budget:
            raise UserError(_("Pick a budget first."))
        segments = self._parse_key(row_key)
        if segments[-1][0] != "A":
            raise UserError(_("Budgets are typed on an account."))
        account_id = _int(segments[-1][1])
        if account_id not in self._path_filter(ctx, segments)["account_ids"]:
            raise UserError(_("This account is not on the report."))
        column = ctx.column(column_key)
        if column not in ctx.budget_columns:
            raise UserError(_("Budgets are typed in the columns of the period on screen."))
        balance = float(amount or 0.0) * (-1 if ctx.sign_for_key(row_key) == "credit" else 1)
        months = []
        cursor = column["date_from"].replace(day=1)
        while cursor <= column["date_to"]:
            months.append(cursor)
            cursor += relativedelta(months=1)
        rounding = ctx.currency.rounding
        share = ctx.currency.round(balance / len(months))
        Line = self.env["odin.pnl.budget.line"]
        for index, month in enumerate(months):
            value = share if index < len(months) - 1 else ctx.currency.round(balance - share * (len(months) - 1))
            existing = Line.search([
                ("budget_id", "=", ctx.budget.id), ("account_id", "=", account_id), ("date", "=", month)])
            if existing:
                existing.balance = value
            elif abs(value) >= rounding:
                Line.create({
                    "budget_id": ctx.budget.id, "account_id": account_id, "date": month, "balance": value})
        return True

    # ------------------------------------------------------------------
    # Saved views
    # ------------------------------------------------------------------

    @api.model
    def list_views(self):
        self._check_access()
        views = self.env["odin.pnl.view"].search([
            "|", ("user_id", "=", self.env.uid), ("shared", "=", True),
        ])
        schedules = dict(self.env["odin.pnl.view"]._fields["schedule"]._description_selection(self.env))
        return [
            {
                "id": view.id,
                "name": view.name,
                "mine": view.user_id == self.env.user,
                "owner": view.user_id.name,
                "shared": view.shared,
                "is_default": view.is_default and view.user_id == self.env.user,
                "period": view.period_rule,
                "schedule": schedules.get(view.schedule) if view.schedule != "none" else False,
            }
            for view in views
        ]

    @api.model
    def save_view(self, options, name, shared=False, view_id=False):
        """Save the page as a view (or overwrite one of the user's own)."""
        self._check_access()
        ctx = self._context(options)
        values = {"options": ctx.options, "shared": bool(shared)}
        if view_id:
            view = self.env["odin.pnl.view"].browse(int(view_id)).exists()
            if not view or view.user_id != self.env.user:
                raise AccessError(_("You can only update your own views."))
            view.write(values)
        else:
            if not (name or "").strip():
                raise UserError(_("Give the view a name."))
            values["name"] = name.strip()
            view = self.env["odin.pnl.view"].create(values)
        return {"id": view.id, "views": self.list_views()}

    @api.model
    def delete_view(self, view_id):
        self._check_access()
        view = self.env["odin.pnl.view"].browse(int(view_id)).exists()
        if view and view.user_id != self.env.user and not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(_("You can only delete your own views."))
        view.unlink()
        return self.list_views()

    @api.model
    def set_default_view(self, view_id):
        self._check_access()
        Views = self.env["odin.pnl.view"]
        if not view_id:
            Views.search([("user_id", "=", self.env.uid), ("is_default", "=", True)]).write({"is_default": False})
            return self.list_views()
        view = Views.browse(int(view_id)).exists()
        if not view:
            raise UserError(_("This view no longer exists."))
        if view.user_id != self.env.user:
            # Someone else's shared view becomes a copy of one's own.
            view = view.copy({"user_id": self.env.uid, "shared": False, "schedule": "none",
                              "recipient_ids": [(5, 0, 0)], "name": view.name})
        view.is_default = True
        return self.list_views()

    @api.model
    def get_view_options(self, view_id=False):
        """The options of a view, or of the user's default one, with the
        period computed again from today (``False`` when there is none)."""
        self._check_access()
        Views = self.env["odin.pnl.view"]
        if view_id:
            view = Views.browse(int(view_id)).exists()
        else:
            view = Views.search([("user_id", "=", self.env.uid), ("is_default", "=", True)], limit=1)
        if not view:
            return False
        return self._normalize_options(view._live_options())

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @api.model
    def _check_accountant(self):
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only accountants can change budgets and notes."))

    @api.model
    def _check_access(self):
        # Every method here is reachable over RPC: menu groups hide, they do
        # not protect. Readonly accountants see the whole ledger anyway.
        if not self.env.user.has_group("account.group_account_readonly"):
            raise AccessError(_("The Profit & Loss is for users who can see accounting."))

    # ------------------------------------------------------------------
    # Options and context
    # ------------------------------------------------------------------

    @api.model
    def _layouts(self):
        return self.env["odin.pnl.layout"].search([
            "|", ("company_id", "=", False), ("company_id", "in", self.env.companies.ids),
        ])

    @api.model
    def _context(self, options):
        return _ReportContext(self, self._normalize_options(options))

    @api.model
    def _normalize_options(self, options):
        """Validate everything the client sends: dates, ids, flags. The
        result is safe to store in a saved view and to send back."""
        options = dict(options or {})
        company = self.env.company
        today = fields.Date.context_today(self)

        def fiscal(day):
            dates = company.compute_fiscalyear_dates(day)
            return dates["date_from"], dates["date_to"]

        date_opt = dict(options.get("date") or {})
        preset = date_opt.get("filter")
        if preset not in periods.PRESETS:
            preset = periods.DEFAULT_PRESET
        if preset == "custom":
            date_from = _parse_date(date_opt.get("date_from"))
            date_to = _parse_date(date_opt.get("date_to"))
            if not date_from or not date_to:
                preset = periods.DEFAULT_PRESET
            elif date_to < date_from:
                date_from, date_to = date_to, date_from
        if preset != "custom":
            date_from, date_to = periods.preset_range(preset, today, fiscal)
        step = _int(date_opt.get("step"))
        if step:
            date_from, date_to = periods.shift(date_from, date_to, step)
            preset = "custom"

        divide = options.get("divide") if options.get("divide") in periods.DIVISIONS else "none"
        comparison = dict(options.get("comparison") or {})
        mode = comparison.get("mode") if comparison.get("mode") in periods.COMPARISONS else "none"
        count = min(max(_int(comparison.get("periods")) or 1, 1), periods.MAX_COMPARISONS)
        custom = None
        if mode == "custom":
            c_from = _parse_date(comparison.get("date_from"))
            c_to = _parse_date(comparison.get("date_to"))
            if c_from and c_to:
                custom = (min(c_from, c_to), max(c_from, c_to))
            else:
                mode = "none"
        if divide != "none":
            # A divided period already shows its months side by side.
            mode = "none"

        layouts = self._layouts()
        layout = layouts.filtered(lambda layout: layout.id == _int(options.get("layout_id")))
        if not layout:
            layout = (
                layouts.filtered(lambda layout: layout.is_default and layout.company_id == company)
                or layouts.filtered(lambda layout: layout.is_default)
                or layouts
            )[:1]
        if not layout:
            raise UserError(_("There is no P&L layout. Create one in Accounting > Configuration > P&L Layouts."))

        budget = self.env["odin.pnl.budget"].browse(_int(options.get("budget_id"))).exists()
        if budget and budget.company_id not in self.env.companies:
            budget = self.env["odin.pnl.budget"]

        unfolded = [key for key in (options.get("unfolded") or []) if isinstance(key, str)][:MAX_UNFOLDED]
        modes = {
            key: mode_value
            for key, mode_value in (options.get("modes") or {}).items()
            if isinstance(key, str) and isinstance(mode_value, str) and key in unfolded
        }
        scale = _int(options.get("scale")) if _int(options.get("scale")) in (1, 1000, 1000000) else 1

        return {
            "layout_id": layout.id,
            "date": {
                "filter": preset,
                "date_from": fields.Date.to_string(date_from),
                "date_to": fields.Date.to_string(date_to),
            },
            "divide": divide,
            "comparison": {
                "mode": mode,
                "periods": count,
                "date_from": custom and fields.Date.to_string(custom[0]),
                "date_to": custom and fields.Date.to_string(custom[1]),
            },
            "journal_ids": self._valid_ids("account.journal", options.get("journal_ids")),
            "partner_ids": self._valid_ids("res.partner", options.get("partner_ids")),
            "analytic_account_ids": self._valid_ids(
                "account.analytic.account", options.get("analytic_account_ids")),
            "include_draft": bool(options.get("include_draft")),
            "hide_zero": options.get("hide_zero", True) is not False,
            "show_codes": options.get("show_codes", True) is not False,
            "account_groups": bool(options.get("account_groups")),
            "percent_of_base": bool(options.get("percent_of_base")),
            "trend": options.get("trend", True) is not False,
            "unfold_all": bool(options.get("unfold_all")),
            "budget_id": budget.id or False,
            "scale": scale,
            "decimals": options.get("decimals", True) is not False,
            "negative_parentheses": bool(options.get("negative_parentheses")),
            "unfolded": unfolded,
            "modes": modes,
        }

    @api.model
    def _valid_ids(self, model, ids):
        ids = [_int(value) for value in (ids or []) if _int(value)]
        if not ids:
            return []
        return self.env[model].search([("id", "in", ids)]).ids

    # ------------------------------------------------------------------
    # Top-level rows: the layout lines
    # ------------------------------------------------------------------

    def _top_rows(self, ctx):
        rows = []
        values = ctx.line_values
        for entry in ctx.parsed_lines:
            line = entry["line"]
            if line.kind == "heading":
                rows.append(self._row(
                    ctx, f"L:{line.code}", None, line.level, "heading", line.name, line=line))
                continue
            row = self._row(
                ctx,
                f"L:{line.code}",
                None,
                line.level,
                "formula" if line.kind == "formula" else "line",
                line.name,
                line=line,
                values=values.get(line.code),
                budget=ctx.line_budgets.get(line.code),
                trend=ctx.line_trends.get(line.code),
            )
            row["unfoldable"] = line.kind == "accounts" and bool(ctx.line_accounts.get(line.code))
            row["mode"] = ctx.options["modes"].get(row["key"], "accounts")
            row["error"] = entry["error"]
            if line.hide_if_zero and _all_zero(row) or (
                ctx.options["hide_zero"] and line.kind == "accounts" and _all_zero(row)
            ):
                continue
            rows.append(row)
        if ctx.unallocated:
            row = self._row(
                ctx, f"L:{UNALLOCATED}", None, 0, "line", _("Unallocated accounts"),
                values=ctx.unallocated_values)
            row["unfoldable"] = True
            row["mode"] = ctx.options["modes"].get(row["key"], "accounts")
            row["warning"] = True
            result_index = next(
                (index for index, top in enumerate(rows) if top.get("is_result")), len(rows))
            rows.insert(result_index, row)
        return rows

    def _row(self, ctx, key, parent, level, row_type, name, line=None, values=None,
             budget=None, trend=None, sign=None):
        """A row as the client renders it. ``values`` are the displayed
        balance figures by column key; derived columns are filled here."""
        sign = sign or (line.sign if line else ctx.sign_for_key(key))
        row = {
            "key": key,
            "parent": parent,
            "level": level,
            "type": row_type,
            "name": name,
            "code": line.code if line else "",
            "values": dict(values or {}),
            "budget": {},
            "budget_pct": {},
            "pct": {},
            "growth": None,
            "growth_good": None,
            "bold": bool(line and (line.bold or line.kind == "formula")),
            "is_result": bool(line and line.is_result),
            "unfoldable": False,
            "unfolded": False,
            "mode": None,
            "sign": sign,
            "green_on_positive": line.green_on_positive if line else ctx.green_for_key(key),
            "trend": trend or [],
            "annotations": ctx.annotation_count(key),
        }
        if row_type == "heading":
            return row
        self._derive(ctx, row, budget)
        return row

    def _derive(self, ctx, row, budget):
        """Budget and % achieved, growth against the first comparison, and %
        of the base line, all from the row's displayed values."""
        values = row["values"]
        if budget is not None:
            for column in ctx.budget_columns:
                planned = budget.get(column["key"])
                row["budget"][column["key"]] = planned
                actual = values.get(column["key"])
                # A percentage only reads when both figures point the same
                # way: a profit line budgeted for its costs alone (budget
                # negative, actual positive) gives nothing meaningful.
                row["budget_pct"][column["key"]] = (
                    actual / planned * 100
                    if planned and actual is not None and actual * planned >= 0
                    else None)
        if ctx.growth_pair:
            current, previous = (values.get(key) for key in ctx.growth_pair)
            if current is not None and previous:
                row["growth"] = (current - previous) / abs(previous) * 100
                row["growth_good"] = (row["growth"] >= 0) == bool(row["green_on_positive"])
        if ctx.options["percent_of_base"] and ctx.base_values:
            for key, value in values.items():
                base = ctx.base_values.get(key)
                row["pct"][key] = value / base * 100 if base and value is not None else None

    # ------------------------------------------------------------------
    # Unfolding
    # ------------------------------------------------------------------

    def _unfold(self, ctx, row, unfolded):
        """Children of an unfolded row, recursively for the ones that are
        unfolded too (restoring a page or exporting it)."""
        result = []
        for child in self._children(ctx, row):
            result.append(child)
            if child["key"] in unfolded and child["unfoldable"]:
                child["unfolded"] = True
                result.extend(self._unfold(ctx, child, unfolded))
        return result

    def _children(self, ctx, parent, offset=0, running=0.0):
        mode = parent["mode"] or self._default_mode(parent["key"])
        if mode == "accounts":
            return self._account_children(ctx, parent)
        if mode in ENTRY_MODES or mode.startswith("audit:"):
            return self._entry_children(ctx, parent, mode, offset, running)
        return self._breakdown_children(ctx, parent, mode)

    def _row_for_key(self, ctx, key):
        """Rebuild the row ``key`` stands for (to unfold it on demand)."""
        segments = self._parse_key(key)
        if not segments or segments[0][0] != "L":
            raise UserError(_("This row cannot be unfolded."))
        filt = self._path_filter(ctx, segments)
        last_kind = segments[-1][0]
        row = {
            "key": key,
            "level": ctx.line_levels.get(segments[0][1], 0) + len(segments) - 1,
            "type": {"L": "line", "G": "group", "A": "account", "B": "breakdown"}.get(last_kind, "line"),
            "mode": ctx.options["modes"].get(key) or self._default_mode(key),
            "sign": ctx.sign_for_key(key),
            "green_on_positive": ctx.green_for_key(key),
            "filter": filt,
        }
        return row

    def _default_mode(self, key):
        last_kind = self._parse_key(key)[-1][0]
        return "accounts" if last_kind in ("L", "G") else "entries"

    def _valid_mode(self, ctx, parent, mode):
        last_kind = self._parse_key(parent["key"])[-1][0]
        if mode == "accounts" and last_kind in ("L", "G"):
            return mode
        if mode in ENTRY_MODES:
            return mode
        if mode.startswith("audit:") and mode[6:] in ctx.columns_by_key:
            return mode
        dim = mode.split(":", 1)[0]
        if dim in DIMENSIONS:
            if dim == "analytic":
                plan_id = _int(mode.split(":", 1)[1] if ":" in mode else 0)
                if not self.env["account.analytic.plan"].browse(plan_id).exists():
                    raise UserError(_("Pick an analytic plan to split by."))
            return mode
        raise UserError(_("This row cannot be split that way."))

    def _account_children(self, ctx, parent):
        filt = parent.get("filter") or self._path_filter(ctx, self._parse_key(parent["key"]))
        account_ids = filt["account_ids"]
        accounts = self.env["account.account"].with_context(active_test=False).browse(account_ids)
        level = parent["level"] + 1
        rows = []
        if ctx.options["account_groups"] and self._parse_key(parent["key"])[-1][0] == "L":
            groups, loose = self._group_tree(accounts)
            for group, group_accounts in groups:
                values = ctx.sum_accounts(group_accounts.ids, parent["sign"])
                row = self._row(
                    ctx, f"{parent['key']}/G:{group.id}", parent["key"], level, "group",
                    group.display_name, values=values,
                    budget=ctx.sum_budget(group_accounts.ids, parent["sign"]),
                    sign=parent["sign"])
                row["unfoldable"] = True
                row["mode"] = ctx.options["modes"].get(row["key"], "accounts")
                if not (ctx.options["hide_zero"] and _all_zero(row)):
                    rows.append(row)
            accounts = loose
        for account in accounts.sorted(lambda acc: (acc.code or acc.placeholder_code or "", acc.name)):
            values = ctx.sum_accounts([account.id], parent["sign"])
            row = self._row(
                ctx, f"{parent['key']}/A:{account.id}", parent["key"], level, "account",
                account.name, values=values,
                budget=ctx.sum_budget([account.id], parent["sign"]),
                trend=ctx.account_trend(account.id, parent["sign"]),
                sign=parent["sign"])
            row["code"] = account.code or account.placeholder_code or ""
            row["account_id"] = account.id
            row["unfoldable"] = True
            row["mode"] = ctx.options["modes"].get(row["key"], "entries")
            row["editable_budget"] = bool(ctx.budget)
            if ctx.options["hide_zero"] and _all_zero(row):
                continue
            rows.append(row)
        return rows

    def _group_tree(self, accounts):
        """The leaf account group of each account (``account.account.group_id``,
        computed from code prefixes); accounts without one stay loose."""
        by_group = defaultdict(lambda: self.env["account.account"])
        loose = self.env["account.account"]
        for account in accounts:
            if account.group_id:
                by_group[account.group_id] |= account
            else:
                loose |= account
        groups = sorted(by_group.items(), key=lambda item: (item[0].code_prefix_start or "", item[0].name))
        return groups, loose

    # ------------------------------------------------------------------
    # Breakdown: "What's in this number"
    # ------------------------------------------------------------------

    def _breakdown_children(self, ctx, parent, mode):
        dim, _sep, arg = mode.partition(":")
        filt = parent.get("filter") or self._path_filter(ctx, self._parse_key(parent["key"]))
        totals = defaultdict(lambda: defaultdict(float))
        aggregated = self._aggregate(
            ctx, filt["account_ids"], ctx.balance_columns, filt["domain"], filt["sets"],
            dim=dim, plan_id=_int(arg))
        for (_account_id, value), amounts in aggregated.items():
            for column, amount in zip(ctx.balance_columns, amounts):
                totals[value][column["key"]] += amount * (-1 if parent["sign"] == "credit" else 1)
        main_key = ctx.balance_columns[0]["key"]
        ordered = sorted(
            (value for value, amounts in totals.items() if any(abs(a) > 1e-9 for a in amounts.values())),
            key=lambda value: (-abs(totals[value][main_key]), str(value)),
        )
        names = self._dimension_names(dim, ordered)
        grand = sum(abs(totals[value][main_key]) for value in ordered) or 0.0
        level = parent["level"] + 1
        show_all = parent.get("show_all")
        visible = ordered if show_all else ordered[:TOP_CONTRIBUTORS]
        rows = []
        for value in visible:
            segment_value = _segment_value(value)
            row = self._row(
                ctx, f"{parent['key']}/B:{dim}{':' + arg if arg else ''}:{segment_value}",
                parent["key"], level, "breakdown", names.get(value) or _("(none)"),
                values=dict(totals[value]), sign=parent["sign"])
            row["share"] = abs(totals[value][main_key]) / grand * 100 if grand else 0.0
            row["dimension"] = dim
            row["unfoldable"] = True
            row["mode"] = ctx.options["modes"].get(row["key"], "entries")
            rows.append(row)
        rest = ordered[TOP_CONTRIBUTORS:] if not show_all else []
        if rest:
            values = defaultdict(float)
            for value in rest:
                for key, amount in totals[value].items():
                    values[key] += amount
            row = self._row(
                ctx, f"{parent['key']}/O:{dim}", parent["key"], level, "others",
                _("Others (%(count)s)", count=len(rest)), values=dict(values), sign=parent["sign"])
            row["share"] = sum(abs(totals[v][main_key]) for v in rest) / grand * 100 if grand else 0.0
            row["others_mode"] = mode
            rows.append(row)
        return rows

    def _dimension_names(self, dim, values):
        ids = [value for value in values if isinstance(value, int) and value]
        if dim == "month":
            return {
                value: format_date(self.env, value, date_format="MMMM yyyy")
                for value in values if isinstance(value, date)
            }
        model = {
            "partner": "res.partner",
            "product": "product.product",
            "product_category": "product.category",
            "journal": "account.journal",
            "analytic": "account.analytic.account",
        }[dim]
        records = self.env[model].with_context(active_test=False).browse(ids).exists()
        names = {record.id: record.display_name for record in records}
        if dim == "analytic":
            names[None] = _("Not assigned")
        return names

    # ------------------------------------------------------------------
    # Journal items: entries, ledger, and the items behind one cell
    # ------------------------------------------------------------------

    def _entry_children(self, ctx, parent, mode, offset, running):
        filt = parent.get("filter") or self._path_filter(ctx, self._parse_key(parent["key"]))
        columns = ctx.balance_columns
        date_from, date_to = ctx.date_min, ctx.date_max
        order = "date desc, id desc"
        if mode == "ledger":
            order = "date asc, id asc"
        elif mode.startswith("audit:"):
            column = ctx.column(mode[6:])
            date_from, date_to = column["date_from"], column["date_to"]
            columns = [column]
        domain = (
            ctx.base_domain
            & Domain("account_id", "in", filt["account_ids"])
            & Domain("date", ">=", date_from)
            & Domain("date", "<=", date_to)
            & filt["domain"]
            & self._analytic_domain(filt["sets"], filt.get("plan"))
        )
        AML = self.env["account.move.line"]
        if mode.startswith("audit:"):
            # Largest first: the item that explains a figure is usually a big one.
            query = AML._search(domain)
            query.order = SQL(
                "ABS(%s) DESC, %s DESC",
                AML._field_to_sql(query.table, "balance", query),
                SQL.identifier(query.table, "id"))
            query.offset = offset
            query.limit = PAGE_SIZE + 1
            lines = AML.browse([row[0] for row in self.env.execute_query(query.select())])
        else:
            lines = AML.search(domain, order=order, offset=offset, limit=PAGE_SIZE + 1)
        has_more = len(lines) > PAGE_SIZE
        lines = lines[:PAGE_SIZE]
        factor = -1 if parent["sign"] == "credit" else 1
        plan_accounts = [int(i) for i in self._plan_account_ids(filt["plan"])] if filt.get("plan") else None
        level = parent["level"] + 1
        rows = []
        for line in lines:
            amount = line.balance * _share(line.analytic_distribution, filt["sets"], plan_accounts) * factor
            values = {
                column["key"]: amount
                for column in columns
                if column["date_from"] <= line.date <= column["date_to"]
            }
            if mode == "ledger":
                running += amount
            row = self._row(
                ctx, f"{parent['key']}/M:{line.id}", parent["key"], level, "entry",
                line.move_name or line.move_id.display_name, values=values, sign=parent["sign"])
            row.update({
                "aml_id": line.id,
                "move_id": line.move_id.id,
                "date": fields.Date.to_string(line.date),
                "partner": line.partner_id.display_name or "",
                "label": line.name or "",
                "journal": line.journal_id.code or "",
                "running": running if mode == "ledger" else None,
                "draft": line.parent_state == "draft",
                "annotations": 0,
            })
            rows.append(row)
        if has_more:
            rows.append({
                "key": f"{parent['key']}/more:{offset + PAGE_SIZE}",
                "parent": parent["key"],
                "level": level,
                "type": "more",
                "name": _("Load more"),
                "offset": offset + PAGE_SIZE,
                "running": running if mode == "ledger" else None,
                "values": {},
            })
        return rows

    # ------------------------------------------------------------------
    # Keys and the journal items a row stands for
    # ------------------------------------------------------------------

    @api.model
    def _parse_key(self, key):
        segments = []
        for part in (key or "").split("/"):
            kind, _sep, value = part.partition(":")
            if not kind:
                continue
            segments.append((kind, value))
        return segments

    def _path_filter(self, ctx, segments):
        """Accounts, extra domain and analytic requirements of a row path."""
        account_ids = None
        domain = Domain.TRUE
        sets = list(ctx.analytic_sets)
        plan = None
        for kind, value in segments:
            if kind == "L":
                if value == UNALLOCATED:
                    account_ids = list(ctx.unallocated)
                else:
                    account_ids = list(ctx.line_accounts.get(value, []))
            elif kind == "G":
                group = self.env["account.group"].browse(_int(value))
                accounts = self.env["account.account"].with_context(active_test=False).browse(account_ids or [])
                account_ids = accounts.filtered(lambda acc: acc.group_id == group).ids
            elif kind == "A":
                account_id = _int(value)
                account_ids = [account_id] if account_id in (account_ids or []) else []
            elif kind == "B":
                dim, _sep, rest = value.partition(":")
                if dim == "analytic":
                    plan_arg, _sep, account_value = rest.partition(":")
                    if account_value == "none":
                        plan = _int(plan_arg)
                    else:
                        sets.append([_int(account_value)])
                else:
                    domain &= self._dimension_domain(dim, rest)
        return {"account_ids": account_ids or [], "domain": domain, "sets": sets, "plan": plan}

    def _dimension_domain(self, dim, value):
        if dim == "month":
            start = _parse_date(f"{value}-01")
            if not start:
                return Domain.FALSE
            return Domain("date", ">=", start) & Domain("date", "<=", date_utils.get_month(start)[1])
        record_id = _int(value) or False
        field = {
            "partner": "partner_id",
            "product": "product_id",
            "journal": "journal_id",
            "product_category": "product_id.categ_id",
        }.get(dim)
        if not field:
            return Domain.FALSE
        if dim == "product_category" and not record_id:
            return Domain("product_id", "=", False)
        return Domain(field, "=", record_id)

    def _analytic_domain(self, sets, plan=None):
        """Only the journal items with a non-zero share of the analytic
        requirements (selected accounts, the account of a breakdown row, or
        the unassigned part of a plan)."""
        if not sets and not plan:
            return Domain.TRUE
        report = self

        def to_sql(model, alias, query):
            return SQL("%s <> 0", report._share_sql(model, alias, query, sets, plan))

        return Domain.custom(to_sql=to_sql)

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def _aggregate(self, ctx, account_ids, columns, extra_domain=Domain.TRUE, sets=(), dim=None, plan_id=None):
        """``{(account_id, dimension value): [amount per column]}``, amounts
        signed like balances (debit positive), in one SQL statement."""
        if not account_ids or not columns:
            return {}
        AML = self.env["account.move.line"]
        date_min = min(column["date_from"] for column in columns)
        date_max = max(column["date_to"] for column in columns)
        domain = (
            ctx.base_domain
            & Domain("account_id", "in", list(account_ids))
            & Domain("date", ">=", date_min)
            & Domain("date", "<=", date_max)
            & extra_domain
        )
        if dim != "analytic":
            domain &= self._analytic_domain(sets)
        query = AML._search(domain)
        alias = query.table
        balance = AML._field_to_sql(alias, "balance", query)
        line_date = AML._field_to_sql(alias, "date", query)
        account = AML._field_to_sql(alias, "account_id", query)
        if dim == "analytic":
            amount = SQL("%s * odin_dim.share", balance)
            self._join_analytic_dimension(AML, alias, query, sets, plan_id)
            dim_sql = SQL("odin_dim.analytic_id")
        else:
            amount = SQL("%s * %s", balance, self._share_sql(AML, alias, query, sets))
            dim_sql = self._dimension_sql(AML, alias, query, dim) if dim else None
        sums = [
            SQL(
                "SUM(CASE WHEN %s BETWEEN %s AND %s THEN %s ELSE 0 END)",
                line_date, column["date_from"], column["date_to"], amount,
            )
            for column in columns
        ]
        if dim_sql is not None:
            query.groupby = SQL("%s, %s", account, dim_sql)
            rows = self.env.execute_query(query.select(account, dim_sql, *sums))
            return {(row[0], row[1]): [float(value or 0.0) for value in row[2:]] for row in rows}
        query.groupby = account
        rows = self.env.execute_query(query.select(account, *sums))
        return {(row[0], None): [float(value or 0.0) for value in row[1:]] for row in rows}

    def _dimension_sql(self, AML, alias, query, dim):
        if dim == "month":
            return SQL("date_trunc('month', %s)::date", AML._field_to_sql(alias, "date", query))
        if dim == "product_category":
            self.env["product.product"].flush_model(["product_tmpl_id"])
            self.env["product.template"].flush_model(["categ_id"])
            product = query.left_join(alias, "product_id", "product_product", "id", "product_id")
            template = query.left_join(product, "product_tmpl_id", "product_template", "id", "product_tmpl_id")
            return SQL.identifier(template, "categ_id")
        field = {"partner": "partner_id", "product": "product_id", "journal": "journal_id"}[dim]
        return AML._field_to_sql(alias, field, query)

    def _share_sql(self, AML, alias, query, sets, plan=None):
        """The share of a journal item counted under the analytic
        requirements: the sum of the distribution percentages whose key
        contains a selected account (for every requirement), over 100.
        ``plan``: the share that no account of that plan covers."""
        if not sets and not plan:
            return SQL("1")
        distribution = SQL(
            "COALESCE(%s, '{}'::jsonb)", AML._field_to_sql(alias, "analytic_distribution", query))
        conditions = SQL(" AND ").join(
            SQL("string_to_array(kv.key, ',') && %s::text[]", [str(i) for i in account_set])
            for account_set in sets
        ) if sets else SQL("TRUE")
        matched = SQL(
            "(SELECT COALESCE(SUM((kv.value)::numeric), 0) / 100"
            " FROM jsonb_each_text(%s) AS kv WHERE %s)",
            distribution, conditions,
        )
        if not plan:
            return matched
        plan_accounts = self._plan_account_ids(plan)
        covered = SQL(
            "(SELECT COALESCE(SUM((kv.value)::numeric), 0) / 100"
            " FROM jsonb_each_text(%s) AS kv WHERE %s AND string_to_array(kv.key, ',') && %s::text[])",
            distribution, conditions, plan_accounts,
        )
        base = matched if sets else SQL("1")
        return SQL("(%s - %s)", base, covered)

    def _join_analytic_dimension(self, AML, alias, query, sets, plan_id):
        """Split each journal item by the accounts of one analytic plan, plus
        the part no account of the plan covers (``analytic_id`` NULL)."""
        plan_accounts = self._plan_account_ids(plan_id)
        distribution = SQL(
            "COALESCE(%s, '{}'::jsonb)", AML._field_to_sql(alias, "analytic_distribution", query))
        conditions = SQL(" AND ").join(
            SQL("string_to_array(kv.key, ',') && %s::text[]", [str(i) for i in account_set])
            for account_set in sets
        ) if sets else SQL("TRUE")
        base = SQL(
            "(SELECT COALESCE(SUM((kv.value)::numeric), 0) / 100 FROM jsonb_each_text(%s) AS kv WHERE %s)",
            distribution, conditions,
        ) if sets else SQL("1")
        lateral = SQL(
            """LATERAL (
                SELECT (k.id)::int AS analytic_id, SUM((kv.value)::numeric) / 100 AS share
                  FROM jsonb_each_text(%(dist)s) AS kv
                 CROSS JOIN LATERAL unnest(string_to_array(kv.key, ',')) AS k(id)
                 WHERE k.id = ANY(%(plan)s::text[]) AND %(cond)s
                 GROUP BY k.id
                UNION ALL
                SELECT NULL::int, %(base)s - (
                    SELECT COALESCE(SUM((kv.value)::numeric), 0) / 100
                      FROM jsonb_each_text(%(dist)s) AS kv
                     WHERE %(cond)s AND string_to_array(kv.key, ',') && %(plan)s::text[])
            )""",
            dist=distribution, plan=plan_accounts, cond=conditions, base=base,
        )
        query.add_join("JOIN", "odin_dim", lateral, SQL("TRUE"))

    def _plan_account_ids(self, plan_id):
        plan = self.env["account.analytic.plan"].browse(plan_id).exists()
        if not plan:
            return []
        accounts = self.env["account.analytic.account"].with_context(active_test=False).search(
            [("root_plan_id", "=", plan.root_id.id)])
        return [str(account_id) for account_id in accounts.ids]


class _ReportContext:
    """Everything one request computes once: options, columns, the layout and
    the account balances it needs. Built by ``odin.pnl.report._context``."""

    def __init__(self, report, options):
        self.report = report
        self.env = report.env
        self.options = options
        self.warnings = []
        self.companies = self.env.companies
        currencies = self.companies.currency_id
        if len(currencies) > 1:
            raise UserError(_(
                "The selected companies keep their books in different currencies "
                "(%(currencies)s). Select companies that share a currency.",
                currencies=", ".join(currencies.mapped("name"))))
        self.currency = currencies[:1] or self.env.company.currency_id
        self.layout = self.env["odin.pnl.layout"].browse(options["layout_id"])
        self.budget = self.env["odin.pnl.budget"].browse(options["budget_id"] or [])
        self._build_columns()
        self.base_domain = self._base_domain()
        self.analytic_sets = [options["analytic_account_ids"]] if options["analytic_account_ids"] else []
        self._load_accounts()
        self._load_values()
        self._load_annotations()

    # ---- columns -------------------------------------------------------

    def _build_columns(self):
        options = self.options
        env = self.env
        company = env.company

        def fiscal(day):
            dates = company.compute_fiscalyear_dates(day)
            return dates["date_from"], dates["date_to"]

        self.fiscal = fiscal
        date_from = fields.Date.to_date(options["date"]["date_from"])
        date_to = fields.Date.to_date(options["date"]["date_to"])
        columns = []
        if options["divide"] != "none":
            for start, end in periods.divide(date_from, date_to, options["divide"]):
                columns.append(self._column(start, end, "current"))
            if len(columns) > 1:
                columns.append(self._column(date_from, date_to, "total", label=_("Total")))
        else:
            columns.append(self._column(date_from, date_to, "current"))
            comparison = options["comparison"]
            custom = None
            if comparison["mode"] == "custom":
                custom = (
                    fields.Date.to_date(comparison["date_from"]),
                    fields.Date.to_date(comparison["date_to"]),
                )
            for start, end in periods.comparisons(
                date_from, date_to, comparison["mode"], comparison["periods"], custom):
                columns.append(self._column(start, end, "comparison"))
        for index, column in enumerate(columns):
            column["key"] = f"c{index}"
        self.balance_columns = columns
        self.columns_by_key = {column["key"]: column for column in columns}
        self.date_min = min(column["date_from"] for column in columns)
        self.date_max = max(column["date_to"] for column in columns)
        self.budget_columns = [column for column in columns if column["group"] in ("current", "total")] \
            if options["budget_id"] else []
        comparisons = [column for column in columns if column["group"] == "comparison"]
        self.growth_pair = (columns[0]["key"], comparisons[0]["key"]) if comparisons else None

    def _column(self, date_from, date_to, group, label=None):
        return {
            "date_from": date_from,
            "date_to": date_to,
            "group": group,
            "label": label or self._label(date_from, date_to),
        }

    def _label(self, date_from, date_to):
        kind = periods.kind_of(date_from, date_to, self.fiscal)
        if kind == "month":
            return format_date(self.env, date_from, date_format="MMM yyyy")
        if kind == "quarter":
            return _("Q%(quarter)s %(year)s",
                     quarter=date_utils.get_quarter_number(date_from), year=date_from.year)
        if kind == "year":
            if date_from.month == 1 and date_from.day == 1:
                return str(date_from.year)
            return _("FY %(start)s-%(end)s", start=date_from.year, end=date_to.year % 100)
        return "%s – %s" % (format_date(self.env, date_from), format_date(self.env, date_to))

    def column(self, key):
        column = self.columns_by_key.get(key)
        if not column:
            raise UserError(_("That column is not on the report any more."))
        return column

    def public_columns(self):
        result = []
        for column in self.balance_columns:
            result.append({
                "key": column["key"],
                "kind": "balance",
                "group": column["group"],
                "label": column["label"],
                "date_from": fields.Date.to_string(column["date_from"]),
                "date_to": fields.Date.to_string(column["date_to"]),
                "budget": column in self.budget_columns,
            })
        return result

    # ---- domain --------------------------------------------------------

    def _base_domain(self):
        options = self.options
        states = ["posted", "draft"] if options["include_draft"] else ["posted"]
        domain = Domain("company_id", "in", self.companies.ids) & Domain("parent_state", "in", states)
        # Line sections and notes carry no amount; keep them out of counts.
        domain &= Domain("display_type", "not in", ("line_section", "line_subsection", "line_note"))
        if options["journal_ids"]:
            domain &= Domain("journal_id", "in", options["journal_ids"])
        if options["partner_ids"]:
            domain &= Domain("partner_id", "in", options["partner_ids"])
        return domain

    # ---- accounts and lines --------------------------------------------

    def _load_accounts(self):
        Account = self.env["account.account"].with_context(active_test=False)
        self.pl_accounts = Account.search([
            ("account_type", "in", PL_ACCOUNT_TYPES),
            ("company_ids", "in", self.companies.ids),
        ])
        pl_ids = set(self.pl_accounts.ids)
        self.parsed_lines = self.layout._parsed_lines()
        self.line_accounts = {}
        self.line_signs = {}
        self.line_green = {}
        self.line_levels = {}
        seen = defaultdict(list)
        for entry in self.parsed_lines:
            line = entry["line"]
            self.line_signs[line.code] = line.sign
            self.line_green[line.code] = line.green_on_positive
            self.line_levels[line.code] = line.level
            if entry["error"]:
                self.warnings.append({"type": "layout", "message": entry["error"]})
            if line.kind != "accounts" or entry["domain"] is None:
                continue
            try:
                ids = Account.search(entry["domain"] & Domain("id", "in", list(pl_ids))).ids
            except (ValueError, KeyError) as error:
                self.warnings.append({"type": "layout", "message": _(
                    "The accounts of %(line)s cannot be found: %(error)s", line=line.name, error=error)})
                ids = []
            self.line_accounts[line.code] = ids
            for account_id in ids:
                seen[account_id].append(line.name)
        self.unallocated = [account_id for account_id in self.pl_accounts.ids if account_id not in seen]
        overlaps = {account_id: names for account_id, names in seen.items() if len(names) > 1}
        if overlaps:
            accounts = Account.browse(list(overlaps))
            self.warnings.append({"type": "overlap", "message": _(
                "%(count)s accounts are counted in more than one line: %(accounts)s.",
                count=len(overlaps),
                accounts=", ".join(
                    "%s (%s)" % (acc.display_name, " + ".join(overlaps[acc.id])) for acc in accounts[:5]),
            )})

    def sign_for_key(self, key):
        first = (key or "").split("/")[0]
        code = first[2:] if first.startswith("L:") else ""
        if code == UNALLOCATED:
            return "credit"
        return self.line_signs.get(code, "debit")

    def green_for_key(self, key):
        first = (key or "").split("/")[0]
        code = first[2:] if first.startswith("L:") else ""
        return self.line_green.get(code, True)

    # ---- values --------------------------------------------------------

    def _load_values(self):
        report = self.report
        aggregated = report._aggregate(self, self.pl_accounts.ids, self.balance_columns, sets=self.analytic_sets)
        self.account_values = {
            account_id: dict(zip((c["key"] for c in self.balance_columns), amounts))
            for (account_id, _dim), amounts in aggregated.items()
        }
        self.budget_values = self._load_budget()
        if self.options["trend"]:
            end = self.date_max.replace(day=1)
            months = [date_utils.get_month(
                end - relativedelta(months=back)) for back in range(TREND_MONTHS - 1, -1, -1)]
            trend_columns = [{"date_from": start, "date_to": stop} for start, stop in months]
            aggregated = report._aggregate(self, self.pl_accounts.ids, trend_columns, sets=self.analytic_sets)
            self.account_trends = {account_id: amounts for (account_id, _dim), amounts in aggregated.items()}
        else:
            self.account_trends = {}

        keys = [column["key"] for column in self.balance_columns]
        self.line_values = {}
        self.line_budgets = {}
        self.line_trends = {}
        for code, ids in self.line_accounts.items():
            sign = self.line_signs[code]
            self.line_values[code] = self.sum_accounts(ids, sign)
            self.line_budgets[code] = self.sum_budget(ids, sign) if self.budget_columns else None
            self.line_trends[code] = self._sum_trend(ids, sign)
        formulas = {
            entry["line"].code: entry["formula"]
            for entry in self.parsed_lines
            if entry["line"].kind == "formula" and entry["formula"] is not None
        }
        self._evaluate_formulas(formulas, keys)
        self.unallocated_values = self.sum_accounts(self.unallocated, "credit")
        base = next((entry["line"].code for entry in self.parsed_lines if entry["line"].is_base), None)
        self.base_values = self.line_values.get(base) or {}
        self._check_integrity(keys)

    def _evaluate_formulas(self, formulas, keys):
        """Fill the formula lines of the balance, budget and trend figures,
        operands first (a formula may use a line further down)."""
        _evaluate(formulas, self.line_values, keys)
        if self.budget_columns:
            _evaluate(formulas, self.line_budgets, [column["key"] for column in self.budget_columns])
        if self.options["trend"]:
            positions = list(range(TREND_MONTHS))
            store = {code: dict(enumerate(values)) for code, values in self.line_trends.items()}
            _evaluate(formulas, store, positions)
            for code in formulas:
                self.line_trends[code] = [store[code].get(position) for position in positions]

    def _check_integrity(self, keys):
        result = next((entry["line"] for entry in self.parsed_lines if entry["line"].is_result), None)
        if not result:
            return
        ledger = {key: 0.0 for key in keys}
        for values in self.account_values.values():
            for key in keys:
                ledger[key] -= values.get(key, 0.0)
        shown = self.line_values.get(result.code) or {}
        rounding = self.currency.rounding
        for column in self.balance_columns:
            key = column["key"]
            difference = (shown.get(key) or 0.0) - ledger[key]
            if abs(difference) > rounding:
                self.warnings.append({"type": "integrity", "message": _(
                    "%(line)s for %(period)s differs from the ledger by %(amount)s: income minus "
                    "expenses of all P&L accounts is %(ledger)s. Check the unallocated or "
                    "double-counted accounts in the layout.",
                    line=result.name, period=column["label"],
                    amount=self.currency.format(difference), ledger=self.currency.format(ledger[key]))})
                break

    def sum_accounts(self, account_ids, sign):
        factor = -1 if sign == "credit" else 1
        totals = {column["key"]: 0.0 for column in self.balance_columns}
        for account_id in account_ids:
            for key, amount in (self.account_values.get(account_id) or {}).items():
                totals[key] += amount * factor
        return totals

    def sum_budget(self, account_ids, sign):
        if not self.budget_columns:
            return None
        factor = -1 if sign == "credit" else 1
        totals = {column["key"]: 0.0 for column in self.budget_columns}
        for account_id in account_ids:
            for key, amount in (self.budget_values.get(account_id) or {}).items():
                totals[key] += amount * factor
        return totals

    def _sum_trend(self, account_ids, sign):
        if not self.options["trend"]:
            return []
        factor = -1 if sign == "credit" else 1
        totals = [0.0] * TREND_MONTHS
        for account_id in account_ids:
            for position, amount in enumerate(self.account_trends.get(account_id) or []):
                totals[position] += amount * factor
        return totals

    def account_trend(self, account_id, sign):
        if not self.options["trend"]:
            return []
        factor = -1 if sign == "credit" else 1
        return [amount * factor for amount in (self.account_trends.get(account_id) or [0.0] * TREND_MONTHS)]

    def _load_budget(self):
        """Budget amounts by account and budget column. A month's amount
        counts in every column that overlaps the month."""
        if not self.budget_columns:
            return {}
        lines = self.env["odin.pnl.budget.line"].search([
            ("budget_id", "=", self.budget.id),
            ("date", ">=", min(column["date_from"] for column in self.budget_columns).replace(day=1)),
            ("date", "<=", max(column["date_to"] for column in self.budget_columns)),
        ])
        values = defaultdict(lambda: defaultdict(float))
        for line in lines:
            for column in self.budget_columns:
                if column["date_from"].replace(day=1) <= line.date <= column["date_to"]:
                    values[line.account_id.id][column["key"]] += line.balance
        return values

    # ---- annotations ---------------------------------------------------

    def _load_annotations(self):
        current = self.balance_columns[0]
        notes = self.env["odin.pnl.annotation"].search([
            ("company_id", "in", self.companies.ids),
            ("layout_id", "in", [False, self.layout.id]),
            "|", ("date", "=", False),
            "&", ("date", ">=", current["date_from"]), ("date", "<=", self.date_max),
        ])
        counts = defaultdict(int)
        for note in notes:
            counts[(note.line_code, note.account_id.id or False)] += 1
        self._annotation_counts = counts

    def annotation_count(self, key):
        segments = (key or "").split("/")
        if not segments or not segments[0].startswith("L:"):
            return 0
        code = segments[0][2:]
        account_id = False
        if len(segments) > 1:
            if not segments[-1].startswith("A:"):
                return 0
            account_id = _int(segments[-1][2:])
        return self._annotation_counts.get((code, account_id), 0)


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def _evaluate(formulas, store, keys):
    """``store[code] = {key: value}`` for every formula code, computing the
    codes a formula uses before the formula itself."""
    done = set()

    def resolve(code, stack=()):
        if code not in formulas or code in done or code in stack:
            return
        tree = formulas[code]
        names = odin_pnl_formula.codes(tree)
        for name in names:
            resolve(name, stack + (code,))
        store[code] = {
            key: odin_pnl_formula.evaluate(tree, {name: (store.get(name) or {}).get(key) for name in names})
            for key in keys
        }
        done.add(code)

    for code in formulas:
        resolve(code)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_date(value):
    try:
        return fields.Date.to_date(value) if value else None
    except (TypeError, ValueError):
        return None


def _all_zero(row):
    amounts = list(row["values"].values()) + list(row.get("budget", {}).values())
    return all(not amount or abs(amount) < 0.005 for amount in amounts)


def _segment_value(value):
    if value is None:
        return "none"
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    return str(value)


def _share(distribution, sets, plan_account_ids=None):
    """Python twin of ``_share_sql`` for journal items listed one by one:
    the share of the item counted under the analytic ``sets`` or, with
    ``plan_account_ids``, the share no account of that plan covers."""
    if not sets and plan_account_ids is None:
        return 1.0
    distribution = distribution or {}
    if isinstance(distribution, str):
        distribution = json.loads(distribution)

    def ids_of(key):
        return {_int(part) for part in key.split(",")}

    def matches(key):
        return all(ids_of(key) & set(account_set) for account_set in sets)

    matched = sum(float(pct) for key, pct in distribution.items() if matches(key)) / 100
    if plan_account_ids is None:
        return matched
    plan_ids = set(plan_account_ids)
    covered = sum(
        float(pct) for key, pct in distribution.items() if matches(key) and ids_of(key) & plan_ids
    ) / 100
    return (matched if sets else 1.0) - covered
