import io
from datetime import date

import xlsxwriter

from odoo import fields, http
from odoo.http import content_disposition, request

MONTH_NAMES = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
               "OCTOBER", "NOVEMBER", "DECEMBER"]
COLUMNS = [
    ("DATE", 11), ("VOUCHER", 15), ("DESCRIPTION", 34), ("PAID TO", 26), ("PHONE NO.", 14), ("METHOD PAID", 11),
    ("M-PESA CODE", 14), ("PAID", 12), ("M-PESA CHARGES", 10), ("TOTAL COST", 12), ("BAL", 13),
    ("CATEGORY", 30), ("ACCOUNT", 30), ("STATUS", 22), ("APPROVED BY", 14),
]


class PettyCashExport(http.Controller):
    """The petty cash book as the team's own workbook: one sheet per month
    (or the one day asked for), the same columns, balance b/f and c/f, totals."""

    @http.route("/odin_petty_cash/export", type="http", auth="user", methods=["POST"])
    def export(self, float_id, year=None, month=None, scope="month", day=None, **kw):
        Float = request.env["odin.petty.cash.float"]
        float_id = int(float_id)
        output = io.BytesIO()
        book = xlsxwriter.Workbook(output, {"in_memory": True})
        fmt = self._formats(book)
        if scope == "day":
            data = Float.get_book_day(float_id, day)
            the_day = fields.Date.to_date(data["day"])
            name = data["float"]["name"]
            heading = f"{the_day.day} {MONTH_NAMES[the_day.month - 1]} {the_day.year}"
            sheet = book.add_worksheet(f"{the_day.day} {MONTH_NAMES[the_day.month - 1][:3]}")
            self._write_sheet(sheet, fmt, f"PETTY CASH BOOK · {heading}", name, the_day, data)
            label = data["day"]
        else:
            year = int(year)
            months = range(1, 13) if scope == "year" else [int(month)]
            name = "Petty cash"
            for m in months:
                data = Float.get_book(float_id, year, m)
                name = data["float"]["name"]
                sheet = book.add_worksheet(MONTH_NAMES[m - 1][:3] if scope == "year" else MONTH_NAMES[m - 1])
                self._write_sheet(sheet, fmt, f"PETTY CASH BOOK {year} · {MONTH_NAMES[m - 1]}", name,
                                  date(year, m, 1), data)
            label = f"{year}" if scope == "year" else f"{year}-{int(month):02d}"
        book.close()
        filename = f"{name} - petty cash book {label}.xlsx"
        return request.make_response(output.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(filename)),
        ])

    def _formats(self, book):
        return {
            "title": book.add_format({"bold": True, "font_size": 14}),
            "sub": book.add_format({"italic": True, "font_color": "#666666"}),
            "head": book.add_format({"bold": True, "bg_color": "#139A4A", "font_color": "#FFFFFF", "border": 1,
                                     "text_wrap": True, "valign": "vcenter"}),
            "cell": book.add_format({"border": 1, "border_color": "#D9D9D9"}),
            "date": book.add_format({"border": 1, "border_color": "#D9D9D9", "num_format": "dd/mm/yyyy"}),
            "money": book.add_format({"border": 1, "border_color": "#D9D9D9", "num_format": "#,##0.00"}),
            "in": book.add_format({"border": 1, "border_color": "#D9D9D9", "num_format": "+#,##0.00",
                                   "font_color": "#139A4A"}),
            "bf": book.add_format({"italic": True, "bg_color": "#F2F2F2", "border": 1, "border_color": "#D9D9D9"}),
            "bf_money": book.add_format({"italic": True, "bold": True, "bg_color": "#F2F2F2", "border": 1,
                                         "border_color": "#D9D9D9", "num_format": "#,##0.00"}),
            "total": book.add_format({"bold": True, "top": 2, "num_format": "#,##0.00"}),
            "total_label": book.add_format({"bold": True, "top": 2}),
        }

    def _write_sheet(self, sheet, fmt, title, name, bf_date, data):
        """One sheet: balance b/f on bf_date, the rows, totals and balance c/f."""
        sheet.write(0, 0, title, fmt["title"])
        sheet.write(1, 0, f"{name} · exported {date.today():%d/%m/%Y} from Odoo", fmt["sub"])
        for col, (heading, width) in enumerate(COLUMNS):
            sheet.write(3, col, heading, fmt["head"])
            sheet.set_column(col, col, width)
        sheet.set_row(3, 30)
        sheet.freeze_panes(4, 3)
        row = 4
        sheet.write_datetime(row, 0, bf_date, fmt["date"])
        sheet.write(row, 1, "BALANCE B/F", fmt["bf"])
        for col in range(2, len(COLUMNS)):
            sheet.write_blank(row, col, None, fmt["bf"])
        sheet.write_number(row, 10, data["opening"], fmt["bf_money"])
        first = row + 1
        for line in data["rows"]:
            row += 1
            y, mo, d = map(int, line["date"].split("-"))
            topup = line["kind"] == "topup"
            values = [
                line["voucher"], line["description"], line["paid_to"], line["phone"],
                "CASH" if line["method"] == "cash" else "MPESA", line["mpesa_code"],
            ]
            sheet.write_datetime(row, 0, date(y, mo, d), fmt["date"])
            for col, value in enumerate(values, start=1):
                sheet.write(row, col, value, fmt["cell"])
            sheet.write_number(row, 7, line["amount"], fmt["in"] if topup else fmt["money"])
            sheet.write_number(row, 8, line["charge"], fmt["money"])
            sheet.write_number(row, 9, 0 if topup else line["total"], fmt["money"])
            sheet.write_number(row, 10, line["balance"], fmt["money"])
            for col, value in enumerate([line["category"], line["account"], line["status"], line["approved_by"]],
                                        start=11):
                sheet.write(row, col, value, fmt["cell"])
        row += 1
        sheet.write(row, 1, "TOTALS / BALANCE C/F", fmt["total_label"])
        if row > first:
            # Cancelled vouchers are listed but, as on the page, neither move
            # the balance nor count in the totals.
            payments = [l for l in data["rows"] if l["kind"] == "payment" and l["state"] != "cancelled"]
            sheet.write_number(row, 7, sum(l["amount"] for l in payments), fmt["total"])
            sheet.write_number(row, 8, sum(l["charge"] for l in payments), fmt["total"])
            sheet.write_number(row, 9, sum(l["total"] for l in payments), fmt["total"])
        sheet.write_number(row, 10, data["closing"], fmt["total"])
        sheet.autofilter(3, 0, max(row - 1, 4), len(COLUMNS) - 1)
