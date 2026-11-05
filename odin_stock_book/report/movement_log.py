from collections import Counter
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import format_date, format_datetime


def _qty(value, zero=""):
    """1500 -> "1,500", 115.5 -> "115.5": as the page shows it. ``zero``:
    what 0 prints as (nothing for an empty column, "0" for on hand)."""
    if not value:
        return zero if value is not None and value is not False else ""
    text = f"{value:,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


class ReportMovementLog(models.AbstractModel):
    """The movement log of a month or a day, from the same rows the page
    shows, as a sheet to sign off and file. Cancelled lines are left out;
    lines not posted yet are marked, and the sheet says it is not final."""

    _name = "report.odin_stock_book.report_movement_log"
    _description = "Stock Book: movement log sheet"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        today = fields.Date.context_today(self)
        day = fields.Date.to_date(data.get("day")) if data.get("day") else None
        year = day.year if day else int(data.get("year") or today.year)
        month = day.month if day else int(data.get("month") or today.month)
        book = self.env["odin.stock.book"].get_book(year, month, warehouse_id=int(data.get("warehouse_id") or 0))
        if book.get("empty"):
            raise UserError(_("Create a warehouse first."))
        rows = [r for r in book["rows"] if r["state"] != "cancelled"
                and (not day or r["date"] == fields.Date.to_string(day))]
        days = []
        for row in rows:
            if not days or days[-1]["date"] != row["date"]:
                days.append({"date": row["date"],
                             "label": format_date(self.env, row["date"], date_format="EEE d MMM"), "rows": []})
            days[-1]["rows"].append(row)
        drafts = [r for r in rows if r["state"] == "draft"]
        warehouse = self.env["stock.warehouse"].browse(book["warehouse"]["id"])
        by_reason = Counter(r["reason_label"] or _("No reason") for r in rows)
        return {
            "doc_ids": [], "doc_model": "odin.stock.line", "docs": self.env["odin.stock.line"],
            "company": warehouse.company_id,
            "warehouse": warehouse,
            "period": format_date(self.env, day, date_format="EEEE d MMMM y") if day
            else format_date(self.env, date(year, month, 1), date_format="MMMM y"),
            "one_day": bool(day),
            "days": days, "line_count": len(rows), "draft_count": len(drafts),
            "posted_count": len(rows) - len(drafts),
            "by_reason": sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0])),
            "printed_on": format_datetime(self.env, fields.Datetime.now(), dt_format="d MMM y, HH:mm"),
            "printed_by": self.env.user.name,
            "qty": _qty,
        }
