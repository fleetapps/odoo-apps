import io
from datetime import date

import xlsxwriter

from odoo import http
from odoo.http import content_disposition, request

MONTH_NAMES = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
               "OCTOBER", "NOVEMBER", "DECEMBER"]


class StockBookExport(http.Controller):
    """The month as the store's own workbook: MOVEMENT LOG and MAIN INVENTORY."""

    @http.route("/odin_stock_book/export", type="http", auth="user", methods=["POST"])
    def export(self, year, month, warehouse_id=None, **kw):
        Book = request.env["odin.stock.book"]
        year, month = int(year), int(month)
        wh = int(warehouse_id) if warehouse_id else False
        book = Book.get_book(year, month, warehouse_id=wh)
        if book.get("empty"):
            return request.not_found()
        sheet_data = Book.get_sheet(year, month, warehouse_id=wh)
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        title = wb.add_format({"bold": True, "font_size": 14})
        head = wb.add_format({"bold": True, "bg_color": "#2E5AAC", "font_color": "#FFFFFF", "border": 1,
                              "text_wrap": True, "valign": "vcenter"})
        cell = wb.add_format({"border": 1, "border_color": "#D9D9D9"})
        num = wb.add_format({"border": 1, "border_color": "#D9D9D9", "num_format": "#,##0.##"})
        money = wb.add_format({"border": 1, "border_color": "#D9D9D9", "num_format": "#,##0.00"})
        sub = wb.add_format({"border": 1, "border_color": "#D9D9D9", "font_color": "#666666", "indent": 2})
        cat = wb.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1, "border_color": "#D9D9D9"})
        cat_money = wb.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1, "border_color": "#D9D9D9",
                                   "num_format": "#,##0.00"})
        dfmt = wb.add_format({"border": 1, "border_color": "#D9D9D9", "num_format": "dd/mm/yyyy"})

        log = wb.add_worksheet("MOVEMENT LOG")
        log.write(0, 0, f"MOVEMENT LOG · {MONTH_NAMES[month - 1]} {year} · {book['warehouse']['name']}", title)
        cols = [("DATE", 11), ("REFERENCE", 15), ("CODE", 15), ("ITEM", 40), ("ADDED", 11), ("REMOVED", 11),
                ("UNIT", 8), ("REASON", 16), ("CUSTOMER / SUPPLIER", 26), ("ON HAND AFTER", 13), ("STATUS", 22),
                ("POSTED BY", 14), ("NOTE", 30)]
        for i, (name, width) in enumerate(cols):
            log.write(2, i, name, head)
            log.set_column(i, i, width)
        log.freeze_panes(3, 4)
        r = 3
        for row in book["rows"]:
            y, m, d = map(int, row["date"].split("-"))
            p = row["product"] or {"code": "", "name": ""}
            log.write_datetime(r, 0, date(y, m, d), dfmt)
            for i, v in enumerate([row["ref"], p["code"], p["name"]], 1):
                log.write(r, i, v, cell)
            log.write(r, 4, row["qty_in"] or "", num)
            log.write(r, 5, row["qty_out"] or "", num)
            for i, v in enumerate([row["uom"], row["reason_label"], row["partner_name"]], 6):
                log.write(r, i, v, cell)
            log.write(r, 9, row["onhand_after"], num)
            for i, v in enumerate([row["status"], row["posted_by"], row["note_print"]], 10):
                log.write(r, i, v, cell)
            r += 1
            for comp in row["components"]:
                log.write(r, 2, comp["product"]["code"], sub)
                log.write(r, 3, comp["product"]["name"], sub)
                log.write(r, 5, comp["qty"], num)
                log.write(r, 6, comp["uom"], cell)
                log.write(r, 7, row["reason_label"], sub)
                r += 1
        log.autofilter(2, 0, max(r - 1, 3), len(cols) - 1)

        inv = wb.add_worksheet("MAIN INVENTORY")
        inv.write(0, 0, f"MAIN INVENTORY · {sheet_data['label']} · {sheet_data['warehouse']['name']}", title)
        cols = [("CODE", 15), ("ITEM", 42), ("OPENING", 12), ("ADDED", 11), ("REMOVED", 11), ("CLOSING", 12),
                ("UNIT", 8), ("UNIT COST", 12), ("VALUE", 15), ("WEEKS OF COVER", 10)]
        for i, (name, width) in enumerate(cols):
            inv.write(2, i, name, head)
            inv.set_column(i, i, width)
        inv.freeze_panes(3, 2)
        r = 3
        for group in sheet_data["groups"]:
            inv.write(r, 0, group["name"], cat)
            for i in range(1, 8):
                inv.write_blank(r, i, None, cat)
            inv.write(r, 8, group["value"], cat_money)
            inv.write_blank(r, 9, None, cat)
            r += 1
            for row in group["rows"]:
                p = row["product"]
                inv.write(r, 0, p["code"], cell)
                inv.write(r, 1, p["name"], cell)
                for i, key in enumerate(["opening", "added", "removed", "closing"], 2):
                    inv.write(r, i, row[key], num)
                inv.write(r, 6, row["uom"], cell)
                inv.write(r, 7, row["cost"], money)
                inv.write(r, 8, row["value"], money)
                inv.write(r, 9, row["cover"] if row["cover"] is not None else "", num)
                r += 1
        inv.write(r, 7, "TOTAL", cat)
        inv.write(r, 8, sheet_data["total"], cat_money)
        wb.close()
        filename = f"Stock book {book['warehouse']['name']} {year}-{month:02d}.xlsx"
        return request.make_response(output.getvalue(), headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(filename)),
        ])
