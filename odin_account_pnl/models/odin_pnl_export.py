"""PDF and Excel of the P&L, as it is on screen.

Exports never trust figures from the browser: the options (period, filters,
unfolded rows and their "By" choice, the detail level) are sent, and the
report is computed again here, as the user. Rows unfolded into journal items
export all of them, not the page on screen.

Laid out the way accountants read a detailed P&L (Xero's and QuickBooks'
"detail" reports): an unfolded row is a heading over its contents with its
figures on a "Total" row below them, and journal items carry their date,
contact and description in columns of their own.

* Excel: xlsxwriter (pinned by Odoo 19's requirements), real numbers with a
  number format, so the workbook can be summed; thousands and millions are a
  format, not a rounding.
  https://xlsxwriter.readthedocs.io/format.html#set_num_format
  Rows are grouped by level (Excel's outline), so the workbook folds back to
  the lines or the accounts; with journal items, a second sheet lists them
  flat, one per row, ready for a pivot table.
  https://xlsxwriter.readthedocs.io/working_with_outlines.html
* PDF: a QWeb report rendered by ``ir.actions.report._render_qweb_pdf``.
  https://www.odoo.com/documentation/19.0/developer/reference/backend/reports.html
"""

import io

import xlsxwriter

from odoo import _, api, fields, models
from odoo.tools import format_datetime, formatLang

MAX_ROWS = 20000
EXPORTED_TYPES = ("heading", "line", "formula", "group", "account", "breakdown", "others", "entry")
# Excel outlines go seven levels deep.
MAX_OUTLINE = 7


class OdinPnlExport(models.AbstractModel):
    _name = "odin.pnl.export"
    _description = "P&L export"

    @api.model
    def render(self, options, fmt):
        """``(content, filename, mimetype)`` of the report in ``fmt``
        (``pdf`` or ``xlsx``)."""
        payload = self._payload(options)
        name = self._filename(payload)
        if fmt == "xlsx":
            return (
                self._xlsx(payload),
                f"{name}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        pdf, _type = self.env["ir.actions.report"]._render_qweb_pdf(
            "odin_account_pnl.action_report_pnl", data={"payload": payload})
        return pdf, f"{name}.pdf", "application/pdf"

    # ------------------------------------------------------------------
    # The data both formats print
    # ------------------------------------------------------------------

    @api.model
    def _payload(self, options):
        Report = self.env["odin.pnl.report"]
        report = Report.with_context(odin_pnl_export=True).get_report(dict(options or {}, trend=False))
        options = report["options"]
        warnings = [warning["message"] for warning in report["warnings"]]
        rows = [row for row in report["rows"] if row["type"] in EXPORTED_TYPES]
        if len(rows) > MAX_ROWS or any(row["type"] == "more" for row in report["rows"]):
            warnings.append(_("Not every journal item is in this export: there are too many. "
                              "Export a shorter period, or fewer accounts."))
        rows = rows[:MAX_ROWS]
        notes = []
        for row in rows:
            row["note_refs"] = []
            if row.get("annotations"):
                for note in Report.get_annotations(options, row["key"]):
                    notes.append({"number": len(notes) + 1, "line": row["name"], "text": note["note"]})
                    row["note_refs"].append(len(notes))
        # The ledger's running balance gets its own column, as on screen.
        running = any(row["type"] == "entry" and row.get("running") is not None for row in rows)
        columns = self._columns(report, running=running)
        # Journal items: date, contact and description in columns of their own.
        items = any(row["type"] == "entry" for row in rows)
        currency = self.env.company.currency_id
        for row in rows:
            row["cells"] = [self._cell(row, column, options, currency) for column in columns]
            if row["type"] == "entry":
                # "label" becomes the printed label: the item's own goes here.
                row["description"] = row.get("label") or ""
            row["label"] = row["name"] if row["type"] == "entry" and items else self._label(row, options)
            row["css"] = " ".join(
                name for name, on in (
                    (row["type"], True), ("bold", row.get("bold")), ("result", row.get("is_result"))) if on)
        layout = self.env["odin.pnl.layout"].browse(options["layout_id"])
        return {
            "title": layout.name or _("Profit & Loss"),
            "company": ", ".join(self.env.companies.mapped("name")),
            # Companies side by side: one period, its columns named after them.
            "period": report["period_label"] if options["by_company"] else " · ".join(
                column["label"] for column in report["columns"] if column["group"] in ("current", "total")),
            "filters": self._filters_text(options),
            "generated": format_datetime(self.env, fields.Datetime.now()),
            "currency": report["currency"]["symbol"],
            "decimals": report["currency"]["decimals"],
            "options": options,
            "columns": columns,
            "items": items,
            "rows": rows,
            "statement": self._statement(rows),
            "notes": notes,
            "warnings": warnings,
        }

    def _statement(self, rows):
        """``rows`` as printed: an unfolded row becomes a heading over its
        contents, and its figures move to a "Total ..." row after them, as in
        the detailed P&L of every accounting package. The scheduled email
        reads ``rows``, where a line keeps its figures."""
        printed = []
        open_rows = []  # unfolded rows whose total is still to print

        def close_until(key):
            while open_rows and not (key or "").startswith(open_rows[-1]["key"] + "/"):
                row = open_rows.pop()
                printed.append(dict(
                    row, key=f"{row['key']}#total", label=self.env._("Total %s", row["name"]),
                    css=f"{row['css']} total bold", note_refs=[]))

        for row in rows:
            close_until(row["key"])
            if row.get("unfolded") and row["type"] in ("line", "group", "account", "breakdown"):
                printed.append(dict(
                    row, css=f"{row['css']} header bold",
                    cells=[dict(cell, value=None, text="") for cell in row["cells"]]))
                open_rows.append(row)
            else:
                printed.append(row)
        close_until(None)
        return printed

    def _columns(self, report, running=False):
        options = report["options"]
        columns = []
        for column in report["columns"]:
            columns.append({"key": column["key"], "kind": "balance", "label": column["label"]})
            if options["percent_of_base"] and column["group"] == "current":
                columns.append({"key": column["key"], "kind": "pct", "label": _("% of revenue")})
            if column["budget"]:
                columns.append({"key": column["key"], "kind": "budget", "label": _("Budget")})
                columns.append({"key": column["key"], "kind": "budget_var", "label": _("Variance")})
                columns.append({"key": column["key"], "kind": "budget_pct", "label": _("% of budget")})
        if running:
            columns.append({"key": "running", "kind": "running", "label": _("Balance")})
        if any(column["group"] == "comparison" for column in report["columns"]):
            columns.append({"key": "growth", "kind": "growth", "label": "%"})
        return columns

    def _cell(self, row, column, options, currency):
        kind = column["kind"]
        if kind == "balance":
            value = row.get("values", {}).get(column["key"])
        elif kind == "pct":
            value = row.get("pct", {}).get(column["key"])
        elif kind == "budget":
            value = row.get("budget", {}).get(column["key"])
        elif kind == "budget_var":
            value = row.get("budget_var", {}).get(column["key"])
        elif kind == "budget_pct":
            value = row.get("budget_pct", {}).get(column["key"])
        elif kind == "running":
            value = row.get("running")
        else:
            value = row.get("growth")
        return {"value": value, "kind": kind, "text": self._format(value, kind, options, currency)}

    def _format(self, value, kind, options, currency):
        if value is None or value is False:
            return ""
        if kind in ("pct", "budget_pct", "growth"):
            sign = "+" if kind == "growth" and value > 0 else ""
            return f"{sign}{formatLang(self.env, value, digits=1)}%"
        scale = options["scale"] or 1
        digits = currency.decimal_places if scale == 1 and options["decimals"] else (1 if scale >= 1000000 else 0)
        text = formatLang(self.env, abs(value) / scale, digits=digits)
        if value < 0:
            return f"({text})" if options["negative_parentheses"] else f"-{text}"
        return text

    def _label(self, row, options):
        if row["type"] == "entry":
            parts = [row.get("date"), row.get("name"), row.get("partner"), row.get("label")]
            return "  ".join(part for part in parts if part)
        if row.get("code") and options["show_codes"] and row["type"] in ("account", "line", "formula"):
            return f"{row['code']}  {row['name']}"
        return row["name"]

    def _filters_text(self, options):
        parts = []
        comparison = options["comparison"]
        if comparison["mode"] != "none":
            parts.append({
                "previous_period": _("compared with the previous period"),
                "previous_year": _("compared with the same period last year"),
                "custom": _("compared with a chosen period"),
            }[comparison["mode"]])
        if options["journal_ids"]:
            parts.append(_("journals: %s", ", ".join(
                self.env["account.journal"].browse(options["journal_ids"]).mapped("code"))))
        if options["analytic_account_ids"]:
            parts.append(_("analytic: %s", ", ".join(
                self.env["account.analytic.account"].browse(options["analytic_account_ids"]).mapped("name"))))
        if options["partner_ids"]:
            parts.append(_("partners: %s", ", ".join(
                self.env["res.partner"].browse(options["partner_ids"]).mapped("display_name"))))
        parts.append(_("posted and draft entries") if options["include_draft"] else _("posted entries"))
        if options["detail"] != "lines":
            parts.append(_("every account") if options["detail"] == "accounts" else _("every transaction"))
        return "; ".join(parts)

    def _filename(self, payload):
        safe = "".join(char if char.isalnum() or char in " -_" else "_" for char in
                       f"{payload['title']} {payload['period']}")
        return " ".join(safe.split())[:120]

    # ------------------------------------------------------------------
    # Excel
    # ------------------------------------------------------------------

    def _xlsx(self, payload):
        buffer = io.BytesIO()
        workbook = xlsxwriter.Workbook(buffer, {"in_memory": True})
        sheet = workbook.add_worksheet(payload["title"][:31])
        options = payload["options"]
        decimals = payload["decimals"] if options["scale"] == 1 and options["decimals"] else 0
        number = "#,##0" + ("." + "0" * decimals if decimals else "")
        if options["scale"] == 1000:
            number += ","
        elif options["scale"] == 1000000:
            number = "#,##0.0,,"
        negative = f"({number})" if options["negative_parentheses"] else f"-{number}"
        number_format = f"{number};{negative};-"
        cache = {}

        def fmt(kind, bold=False, indent=0):
            key = (kind, bold, indent)
            if key not in cache:
                props = {"bold": bold, "font_size": 10}
                if kind == "number":
                    props["num_format"] = number_format
                elif kind == "percent":
                    props["num_format"] = "0.0%"
                else:
                    props["indent"] = indent
                cache[key] = workbook.add_format(props)
            return cache[key]

        title = workbook.add_format({"bold": True, "font_size": 14})
        muted = workbook.add_format({"font_color": "#6c757d", "font_size": 9})
        head = workbook.add_format({"bold": True, "bottom": 1, "align": "right", "font_size": 10})
        head_left = workbook.add_format({"bold": True, "bottom": 1, "font_size": 10})
        total_line = workbook.add_format({"bold": True, "top": 1, "num_format": number_format, "font_size": 10})
        date_format = workbook.add_format({"num_format": "yyyy-mm-dd", "font_size": 10})
        sheet.write(0, 0, payload["title"], title)
        sheet.write(1, 0, f"{payload['company']} · {payload['period']}", muted)
        sheet.write(2, 0, payload["filters"], muted)
        if payload["warnings"]:
            sheet.write(3, 0, " ".join(payload["warnings"]), muted)
        header_row = 4
        # Journal items: their date, contact and description before the figures.
        details = [_("Date"), _("Contact"), _("Description")] if payload["items"] else []
        first = 1 + len(details)
        sheet.write(header_row, 0, _("Account (%s)", payload["currency"]), head_left)
        for index, label in enumerate(details, start=1):
            sheet.write(header_row, index, label, head_left)
        for index, column in enumerate(payload["columns"], start=first):
            sheet.write(header_row, index, column["label"], head)
        # The fold button on the heading, not on the Total row Excel would
        # pick: folded, an account reads as its name over its total.
        sheet.outline_settings(True, False, True, False)
        row_index = header_row
        for row in payload["statement"]:
            row_index += 1
            level = min(row.get("level") or 0, MAX_OUTLINE)
            if level:
                sheet.set_row(row_index, None, None, {"level": level})
            bold = "bold" in row["css"].split() or row["type"] == "heading"
            label = row["label"] + "".join(f" [{ref}]" for ref in row.get("note_refs", []))
            sheet.write(row_index, 0, label, fmt("text", bold, min(row.get("level") or 0, 10)))
            if row["type"] == "entry" and details:
                if row.get("date"):
                    sheet.write_datetime(row_index, 1, fields.Date.to_date(row["date"]), date_format)
                sheet.write(row_index, 2, row.get("partner") or "", fmt("text"))
                sheet.write(row_index, 3, row["description"], fmt("text"))
            for index, cell in enumerate(row["cells"], start=first):
                value = cell["value"]
                if value is None:
                    continue
                if cell["kind"] in ("pct", "budget_pct", "growth"):
                    sheet.write_number(row_index, index, value / 100, fmt("percent", bold))
                elif "total" in row["css"].split():
                    sheet.write_number(row_index, index, value, total_line)
                else:
                    sheet.write_number(row_index, index, value, fmt("number", bold))
        if payload["notes"]:
            row_index += 2
            sheet.write(row_index, 0, _("Notes"), head_left)
            for note in payload["notes"]:
                row_index += 1
                sheet.write(row_index, 0, f"[{note['number']}] {note['line']}: {note['text']}", muted)
        sheet.set_column(0, 0, 52)
        if details:
            sheet.set_column(1, 1, 11)
            sheet.set_column(2, 3, 28)
        sheet.set_column(first, first + len(payload["columns"]) - 1, 15)
        sheet.freeze_panes(header_row + 1, 1)
        if payload["items"]:
            self._xlsx_items(workbook, payload, number_format, date_format, head, head_left)
        workbook.close()
        return buffer.getvalue()

    def _xlsx_items(self, workbook, payload, number_format, date_format, head, head_left):
        """The journal items of the statement, one per row with their line
        and account, for a pivot table or a filter: the layout accountants
        otherwise rebuild by hand from an outlined export."""
        sheet = workbook.add_worksheet(_("Transactions")[:31])
        lines, accounts = {}, {}
        for row in payload["rows"]:
            if row["type"] in ("line", "formula") and not row.get("parent"):
                lines[row["key"]] = row["name"]
            elif row["type"] == "account":
                accounts[row["key"]] = (row.get("code") or "", row["name"])
        labels = [_("Line"), _("Account code"), _("Account"), _("Date"), _("Journal"), _("Reference"),
                  _("Contact"), _("Description"), _("Amount"), _("Debit − credit")]
        for index, label in enumerate(labels):
            sheet.write(0, index, label, head if index >= 8 else head_left)
        number = workbook.add_format({"num_format": number_format, "font_size": 10})
        text = workbook.add_format({"font_size": 10})
        row_index = 0
        for row in payload["rows"]:
            if row["type"] != "entry":
                continue
            row_index += 1
            segments = row["key"].split("/")
            ancestors = ["/".join(segments[:size]) for size in range(len(segments) - 1, 0, -1)]
            code, account = next((accounts[key] for key in ancestors if key in accounts), ("", ""))
            sheet.write(row_index, 0, lines.get(segments[0], ""), text)
            sheet.write(row_index, 1, code, text)
            sheet.write(row_index, 2, account, text)
            if row.get("date"):
                sheet.write_datetime(row_index, 3, fields.Date.to_date(row["date"]), date_format)
            sheet.write(row_index, 4, row.get("journal") or "", text)
            sheet.write(row_index, 5, row["name"] or "", text)
            sheet.write(row_index, 6, row.get("partner") or "", text)
            sheet.write(row_index, 7, row["description"], text)
            amount = row.get("amount") or 0.0
            sheet.write_number(row_index, 8, amount, number)
            sheet.write_number(row_index, 9, -amount if row.get("sign") == "credit" else amount, number)
        sheet.autofilter(0, 0, max(row_index, 1), len(labels) - 1)
        sheet.freeze_panes(1, 0)
        sheet.set_column(0, 0, 24)
        sheet.set_column(1, 1, 12)
        sheet.set_column(2, 2, 28)
        sheet.set_column(3, 5, 14)
        sheet.set_column(6, 7, 30)
        sheet.set_column(8, 9, 15)


class ReportOdinPnl(models.AbstractModel):
    """Values of the PDF template ``odin_account_pnl.report_pnl``."""

    _name = "report.odin_account_pnl.report_pnl"
    _description = "P&L PDF"

    @api.model
    def _get_report_values(self, docids, data=None):
        return {"payload": (data or {}).get("payload") or {}}
