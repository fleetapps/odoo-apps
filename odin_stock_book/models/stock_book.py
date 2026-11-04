import re
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .stock_book_line import MAKES, NEED_KEYS, REASONS, REVERSE

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
PASTE_LIMIT = 1000

# Words a store's own sheet uses for each reason, for rows pasted from Excel.
REASON_WORDS = {
    "filling": {"filling", "bottle filling", "fill", "filled", "fil"},
    "production": {"production", "production batch", "batch", "produced", "made", "prd"},
    "received": {"received", "receipt", "receive", "purchase", "purchased", "grn", "supplier"},
    "return": {"return", "customer return", "returned", "returns", "ret"},
    "sale": {"sale", "sales", "sold", "delivery", "dispatch", "dispatched", "invoice"},
    "spoiled": {"spoiled", "spoilt", "damaged", "damage", "expired", "broken", "scrap", "scrapped", "scr"},
    "sample": {"sample", "samples", "gift", "sample / gift", "sample/gift", "free", "smp"},
    "used": {"used", "used in-house", "used in house", "in-house", "in house", "internal use", "use"},
    "correction": {"correction", "adjustment", "adj", "stock take", "stocktake", "count"},
}
# Column headings of a pasted sheet, and the column each one fills.
HEADINGS = {
    "date": "date", "day": "date",
    "code": "code", "item code": "code", "sku": "code", "product code": "code",
    "item": "item", "product": "item", "description": "item", "name": "item", "item name": "item",
    "added": "added", "in": "added", "qty in": "added", "quantity in": "added",
    "removed": "removed", "out": "removed", "qty out": "removed", "quantity out": "removed",
    "reason": "reason", "type": "reason", "movement": "reason",
    "customer": "partner", "supplier": "partner", "customer / supplier": "partner",
    "customer/supplier": "partner", "partner": "partner",
    "note": "note", "notes": "note", "remarks": "note", "comment": "note",
}
# Without headings: the columns of the store's MOVEMENT LOG, in its order.
DEFAULT_COLUMNS = ["date", "item", "added", "removed", "reason", "partner"]
MONTH_KEYS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _key(text):
    return " ".join(str(text or "").lower().split())


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_qty(text):
    """"1,500", "24 pcs" or "-3" -> number; None when there is no number."""
    match = re.search(r"-?\d[\d,\s]*(?:\.\d+)?", str(text or ""))
    if not match:
        return None
    return float(re.sub(r"[,\s]", "", match.group()))


def parse_day(text, year, month):
    """A day the quick way, Kenyan order, as the page reads it: 3, 3/11,
    3/11/26, 3 Nov or 2026-11-03. None when it is not a day."""
    value = _key(text)
    if not value:
        return None
    d, m, y = None, month, year
    if match := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t].*)?", value):
        y, m, d = int(match[1]), int(match[2]), int(match[3])
    elif match := re.fullmatch(r"(\d{1,2})", value):
        d = int(match[1])
    elif match := re.fullmatch(r"(\d{1,2})[/.\- ](\d{1,2})(?:[/.\- ](\d{2,4}))?", value):
        d, m = int(match[1]), int(match[2])
        y = int(("20" + match[3]) if len(match[3] or "") == 2 else match[3]) if match[3] else year
    elif match := re.fullmatch(r"(\d{1,2})[\s\-/.]*([a-z]{3})[a-z]*\.?(?:[\s\-/.,]+(\d{2,4}))?", value):
        if match[2] not in MONTH_KEYS:
            return None
        d, m = int(match[1]), MONTH_KEYS.index(match[2]) + 1
        y = int(("20" + match[3]) if len(match[3] or "") == 2 else match[3]) if match[3] else year
    else:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


class StockBook(models.AbstractModel):
    """What the Stock Book page reads and writes. One place, so the page
    makes few calls and every number comes from the same rules."""

    _name = "odin.stock.book"
    _description = "Stock Book"

    # ------------------------------------------------------------ helpers
    @api.model
    def _warehouses(self):
        return self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)])

    @api.model
    def _warehouse(self, warehouse_id=False):
        warehouses = self._warehouses()
        return warehouses.filtered(lambda w: w.id == warehouse_id)[:1] or warehouses[:1]

    @api.model
    def _product(self, product):
        return {"id": product.id, "code": product.default_code or "", "name": product.name,
                "display_name": product.display_name, "uom": product.uom_id.name} if product else False

    @api.model
    def _is_supervisor(self):
        return self.env.user.has_group("odin_stock_book.group_stock_book_supervisor")

    @api.model
    def _utc(self, day):
        """Midnight at the start of ``day`` where the user is, in UTC as the
        database keeps it: a month's movements are the user's month."""
        tz = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        return tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)

    @api.model
    def _stock_value(self, wh):
        """On hand times cost, as the stock sheet values it. Odoo's own
        valuation field on quants is for stock managers only."""
        quants = self.env["stock.quant"]._read_group(
            [("location_id", "child_of", wh.lot_stock_id.id)], ["product_id"], ["quantity:sum"])
        return sum(qty * product.with_company(wh.company_id).standard_price for product, qty in quants)

    # ------------------------------------------------------------ movements
    @api.model
    def get_book(self, year=False, month=False, reason=False, status=False, search="", warehouse_id=False):
        wh = self._warehouse(warehouse_id)
        if not wh:
            return {"empty": True}
        today = fields.Date.context_today(self)
        year, month = year or today.year, month or today.month
        start = date(year, month, 1)
        end = start + relativedelta(months=1)
        Line = self.env["odin.stock.line"]
        lines = Line.search([("warehouse_id", "=", wh.id), ("date", ">=", start), ("date", "<", end)],
                            order="date, id")
        all_drafts = Line.search([("warehouse_id", "=", wh.id), ("state", "=", "draft")], order="date, id")
        products = (lines | all_drafts).product_id | (lines | all_drafts).component_ids.product_id
        available = Line._available(products, wh)

        # Drafts are projected in date order on top of what is on hand now,
        # so "on hand after" and "short" read like the sheet's running balance.
        projected = dict(available)
        draft_view = {}
        for line in all_drafts:
            line_status = line._status(projected)
            if line_status[0] in ("ready", "short"):
                projected[line.product_id.id] = projected.get(line.product_id.id, 0.0) + line.qty_in - line.qty_out
                for comp in line.component_ids:
                    projected[comp.product_id.id] = projected.get(comp.product_id.id, 0.0) - comp.qty
            draft_view[line.id] = (line_status, projected.get(line.product_id.id, 0.0),
                                   {c.id: projected.get(c.product_id.id, 0.0) for c in line.component_ids})

        reversals = Line.search([("reverses_id", "in", lines.ids), ("state", "!=", "cancelled")])
        reversed_by = {r.reverses_id.id: r.ref or _("a draft line") for r in reversals}
        rows = []
        counts = {"need": 0, "ready": 0, "short": 0, "posted": 0}
        for line in lines:
            if line.state == "draft":
                line_status, onhand, comp_onhand = draft_view[line.id]
            else:
                line_status, onhand, comp_onhand = line._status(), line.onhand_after, {}
            key = line_status[0]
            if key in ("posted", "ready", "short"):
                counts[key] += 1
            elif key != "cancelled":
                counts["need"] += 1
            rows.append(self._row(line, line_status, onhand, comp_onhand, reversed_by.get(line.id)))
        if reason:
            groups = {"make": MAKES, "sale": {"sale", "return"}, "in": {"received"},
                      "out": {"spoiled", "sample", "used"}, "correction": {"correction"}}
            keep = groups.get(reason, {reason})
            rows = [r for r in rows if r["reason"] in keep]
        if status:
            keep = {"need": NEED_KEYS | {"short"}, "ready": {"ready"}, "posted": {"posted"}}.get(status, {status})
            rows = [r for r in rows if r["status_key"] in keep]
        if search:
            needle = search.lower()
            rows = [r for r in rows if needle in " ".join(filter(None, [
                r["ref"], r["product"] and r["product"]["display_name"], r["partner_name"], r["note"],
                r["reason_label"]] + [c["product"]["display_name"] for c in r["components"]])).lower()]

        per_month = dict.fromkeys(range(1, 13), 0)
        for group_month, count in Line._read_group(
                [("warehouse_id", "=", wh.id), ("state", "!=", "cancelled"),
                 ("date", ">=", date(year, 1, 1)), ("date", "<", date(year + 1, 1, 1))],
                ["date:month"], ["__count"]):
            per_month[group_month.month] = count
        older = all_drafts.filtered(lambda l: l.date < start)
        return {
            "warehouse": {"id": wh.id, "name": wh.name},
            "warehouses": [{"id": w.id, "name": w.name} for w in self._warehouses()],
            "year": year, "month": month, "today": fields.Date.to_string(today),
            "months": [{"month": i, "label": MONTHS[i - 1], "count": per_month[i]} for i in range(1, 13)],
            "rows": rows, "counts": counts,
            "drafts_total": len(all_drafts),
            "older_open": {"count": len(older), "year": older[:1].date.year if older else False,
                           "month": older[:1].date.month if older else False},
            "stock_value": self._stock_value(wh), "currency": self.env.company.currency_id.symbol,
            "out_in_recipes": self._out_in_recipes(wh),
            "reasons": [{"key": k, "label": v[1], "direction": v[0]} for k, v in REASONS.items()],
            "can_post": self._is_supervisor(),
        }

    @api.model
    def _row(self, line, status, onhand, comp_onhand, reversed_by=None):
        key, label, tone, detail = status
        recipes = line._recipes() if line.reason in MAKES and line.state == "draft" else line.bom_id
        return {
            "id": line.id, "date": fields.Date.to_string(line.date), "ref": line.ref or "",
            "reason": line.reason or "", "reason_label": REASONS[line.reason][1] if line.reason else "",
            "posts_as": REASONS[line.reason][2] if line.reason else "",
            "direction": REASONS[line.reason][0] if line.reason else "both",
            "product": self._product(line.product_id), "uom": line.uom_id.name or "",
            "qty_in": line.qty_in, "qty_out": line.qty_out, "partner_name": line.partner_name or "",
            "note": line.note or "", "unit_cost": line.unit_cost, "state": line.state,
            "status_key": key, "status": label, "tone": tone, "detail": detail, "onhand_after": onhand,
            "bom_id": line.bom_id.id, "recipes": [[b.id, b.code or b.display_name] for b in recipes],
            "components": [{
                "id": c.id, "product": self._product(c.product_id), "uom": c.uom_id.name, "qty": c.qty,
                "from_recipe": c.from_recipe, "onhand_after": comp_onhand.get(c.id),
                "unlimited": line._unlimited(c.product_id),
            } for c in line.component_ids],
            "posted_by": line.posted_by_id.name or "", "has_document": bool(line.move_ids),
            "reverses": line.reverses_id.ref or "", "reversed_by": reversed_by or "",
            "can_reverse": line.state == "posted" and line.reason in REVERSE and not reversed_by,
        }

    @api.model
    def _out_in_recipes(self, wh):
        """Components the recipes need that are at zero or below: what stops
        the next filling or batch."""
        bom_products = self.env["mrp.bom.line"].search([("bom_id.active", "=", True)]).product_id.filtered(
            "is_storable")
        available = self.env["odin.stock.line"]._available(bom_products, wh)
        out = bom_products.filtered(lambda p: available[p.id] <= 0 and not p.stock_book_unlimited)
        return {"count": len(out), "examples": out[:3].mapped("name")}

    @api.model
    def book_save(self, line_id, values, keep_column=False):
        """``keep_column``: the page undoing "the quantity is in Removed": the
        number stays in the column it was typed in."""
        allowed = {"date", "reason", "product_id", "qty_in", "qty_out", "partner_name", "note", "unit_cost",
                   "bom_id", "warehouse_id"}
        values = {k: v for k, v in values.items() if k in allowed}
        Line = self.env["odin.stock.line"].with_context(odin_stock_keep_column=bool(keep_column))
        if line_id:
            line = Line.browse(line_id)
            meant_in, meant_out = values.get("qty_in", line.qty_in), values.get("qty_out", line.qty_out)
            line.write(values)
        else:
            values["warehouse_id"] = self._warehouse(values.get("warehouse_id")).id
            meant_in, meant_out = values.get("qty_in"), values.get("qty_out")
            line = Line.create(values)
        row = self._row(line, line._status(),
                        Line._available(line.product_id, line.warehouse_id).get(line.product_id.id, 0.0), {})
        # Tell the page when the reason took the quantity to its own column.
        row["moved_to"] = ("removed" if meant_in and not meant_out and not line.qty_in and line.qty_out
                           else "added" if meant_out and not meant_in and not line.qty_out and line.qty_in
                           else "")
        return row

    @api.model
    def book_component(self, line_id, component_id=False, product_id=False, qty=None, remove=False):
        Component = self.env["odin.stock.line.component"]
        line = self.env["odin.stock.line"].browse(line_id)
        if line.state != "draft":
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))
        component = Component.browse(component_id) if component_id else Component
        if component and component not in line.component_ids:
            raise UserError(_("That component belongs to another line."))
        if remove and component:
            component.unlink()
        elif component:
            component.write({"qty": max(qty or 0.0, 0.0)})
        elif product_id:
            Component.create({"line_id": line.id, "product_id": product_id, "qty": qty or 0.0,
                              "sequence": len(line.component_ids) + 1})
        return True

    @api.model
    def book_reset_components(self, line_id):
        self.env["odin.stock.line"].browse(line_id)._explode()
        return True

    @api.model
    def book_post(self, ids, allow_short=False):
        lines = self.env["odin.stock.line"].browse(ids).exists().filtered(lambda l: l.state == "draft")
        if not allow_short:
            lines = lines.filtered(lambda l: l._status()[0] == "ready")
        lines.action_post(allow_short=allow_short)
        posted = lines.filtered(lambda l: l.state == "posted").sorted("ref")
        return {"count": len(posted), "refs": posted.mapped("ref")}

    @api.model
    def book_cancel(self, ids):
        self.env["odin.stock.line"].browse(ids).action_cancel()
        return True

    @api.model
    def book_restore(self, ids):
        """Undo of Cancel line: the cancelled lines are drafts again."""
        lines = self.env["odin.stock.line"].browse(ids).exists()
        lines.action_draft()
        return len(lines.filtered(lambda l: l.state == "draft"))

    @api.model
    def book_discard(self, ids):
        """Undo of Copy, Reverse and Paste: the drafts they added go, without a
        trace. A line posted in the meantime stays (and is counted)."""
        lines = self.env["odin.stock.line"].browse(ids).exists()
        drafts = lines.filtered(lambda l: l.state in ("draft", "cancelled"))
        kept = len(lines - drafts)
        drafts.unlink()
        return {"removed": len(drafts), "kept": kept}

    @api.model
    def book_print(self, year=False, month=False, day=False, warehouse_id=False):
        """The movement log of a month, or of one day, as a sheet to sign."""
        wh = self._warehouse(warehouse_id)
        if not wh:
            raise UserError(_("Create a warehouse first."))
        data = {"year": year, "month": month, "day": day or False, "warehouse_id": wh.id}
        return self.env.ref("odin_stock_book.action_report_movement_log").report_action(
            [], data=data, config=False)

    @api.model
    def book_copy(self, line_id, day=False):
        line = self.env["odin.stock.line"].browse(line_id)
        copy = line._copy_as_draft(day or fields.Date.context_today(self))
        return copy.id

    @api.model
    def book_reverse(self, line_id):
        return self.env["odin.stock.line"].browse(line_id)._reverse().id

    @api.model
    def book_suggest_products(self, text, warehouse_id=False):
        """Items for the Item cell: code or name, with what is on hand and the
        reason this item is usually logged with."""
        words = (text or "").split()
        if not words:
            return []
        # Every word typed must appear in the code or the name, in any order:
        # "lush 500" finds Lush Green Handwash 500 Ml.
        domain = [("is_storable", "=", True)]
        for word in words:
            domain += ["|", ("default_code", "ilike", word), ("name", "ilike", word)]
        Product = self.env["product.product"]
        exact = Product.search([("is_storable", "=", True), ("default_code", "=ilike", _escape_like(text.strip()))],
                               limit=1)
        products = (exact | Product.search(domain, limit=8, order="default_code"))[:8]
        wh = self._warehouse(warehouse_id)
        available = self.env["odin.stock.line"]._available(products, wh)
        boms = self.env["mrp.bom"].search([("product_tmpl_id", "in", products.product_tmpl_id.ids)])
        has_recipe = set(boms.product_tmpl_id.ids)
        result = []
        for p in products:
            usual = self.env["odin.stock.line"].search([("product_id", "=", p.id), ("reason", "!=", False)],
                                                       order="id desc", limit=1).reason
            if not usual:
                usual = ("production" if "bulk" in (p.categ_id.complete_name or "").lower() else "filling") \
                    if p.product_tmpl_id.id in has_recipe else "received"
            result.append({**self._product(p), "onhand": available[p.id], "has_recipe": p.product_tmpl_id.id in has_recipe,
                           "usual_reason": usual, "tracked": p.tracking != "none"})
        return result

    @api.model
    def book_suggest_partners(self, text):
        if not text or len(text) < 2:
            return []
        # Through the ORM, so a company only sees the names its own lines use.
        names = [name for name, _count in self.env["odin.stock.line"]._read_group(
            [("partner_name", "ilike", text)], ["partner_name"], ["__count"], order="__count desc", limit=6)]
        for partner in self.env["res.partner"].search([("name", "ilike", text), ("parent_id", "=", False)], limit=6):
            if partner.name.upper() not in names:
                names.append(partner.name.upper())
        return names[:8]

    # ------------------------------------------------------------ paste from Excel
    @api.model
    def _paste_columns(self, first_row):
        """Columns by heading when the first row is a heading row, else the
        order of the MOVEMENT LOG."""
        mapped = [HEADINGS.get(_key(cell)) for cell in first_row]
        if sum(1 for m in mapped if m) >= 2:
            return mapped, True
        return DEFAULT_COLUMNS, False

    @api.model
    def _paste_product(self, item, code, cache):
        key = (_key(code), _key(item))
        if key in cache:
            return cache[key]
        Product = self.env["product.product"]
        base = [("is_storable", "=", True)]
        found = Product
        for value in filter(None, [(code or "").strip(), (item or "").strip()]):
            for field in ("default_code", "name"):
                match = Product.search(base + [(field, "=ilike", _escape_like(value))], limit=2)
                if len(match) == 1:
                    found = match
                    break
            if found:
                break
        if not found and (item or "").strip():
            # "[FIN-PAK-002] Aqua Blue ..." as Odoo prints it, or every word of the name.
            if bracket := re.match(r"\s*\[([^\]]+)\]", item):
                found = Product.search(base + [("default_code", "=ilike", _escape_like(bracket[1].strip()))], limit=2)
            if len(found) != 1:
                domain = list(base)
                for word in item.split():
                    domain += ["|", ("default_code", "ilike", word), ("name", "ilike", word)]
                found = Product.search(domain, limit=2)
        cache[key] = found if len(found) == 1 else Product
        return cache[key]

    @api.model
    def book_paste(self, text, year=False, month=False, warehouse_id=False):
        """Rows copied from a spreadsheet, one draft line each. Rows whose item
        cannot be told apart are left out and listed, with the reason."""
        wh = self._warehouse(warehouse_id)
        if not wh:
            raise UserError(_("Create a warehouse first."))
        today = fields.Date.context_today(self)
        year, month = year or today.year, month or today.month
        default_day = today if (today.year, today.month) == (year, month) else date(year, month, 1)
        rows = [r.split("\t") for r in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            return {"created": 0, "skipped": [], "notes": []}
        columns, has_heading = self._paste_columns(rows[0])
        if has_heading:
            rows = rows[1:]
        if len(rows) > PASTE_LIMIT:
            raise UserError(_("Paste at most %s rows at a time.", PASTE_LIMIT))
        reasons = {word: key for key, words in REASON_WORDS.items() for word in words}
        reasons.update({_key(label): key for key, (_d, label, _p, _r) in REASONS.items()})
        vals_list, skipped, notes, cache = [], [], [], {}
        last_day = default_day
        for number, cells in enumerate(rows, start=2 if has_heading else 1):
            cell = {col: (cells[i].strip() if i < len(cells) else "") for i, col in enumerate(columns) if col}
            raw = " · ".join(c.strip() for c in cells if c.strip())[:120]
            day = last_day
            if cell.get("date"):
                day = parse_day(cell["date"], year, month)
                if not day:
                    skipped.append({"row": number, "text": raw, "why": _("“%s” is not a date", cell["date"])})
                    continue
            product = self._paste_product(cell.get("item"), cell.get("code"), cache)
            if not product:
                what = cell.get("code") or cell.get("item")
                skipped.append({"row": number, "text": raw, "why": _("no single item matches “%s”", what)
                                if what else _("no item")})
                continue
            qty_in, qty_out = parse_qty(cell.get("added")), parse_qty(cell.get("removed"))
            if qty_in is not None and qty_in < 0:
                qty_in, qty_out = None, -qty_in
            if qty_out is not None and qty_out < 0:
                qty_in, qty_out = -qty_out, None
            if not qty_in and not qty_out:
                skipped.append({"row": number, "text": raw, "why": _("no quantity")})
                continue
            reason = False
            if cell.get("reason"):
                reason = reasons.get(_key(cell["reason"]))
                if not reason:
                    notes.append(_("Row %(row)s: “%(reason)s” is not a reason the book knows: pick one in the row.",
                                   row=number, reason=cell["reason"]))
            last_day = day
            vals_list.append({
                "date": day, "product_id": product.id, "warehouse_id": wh.id,
                "qty_in": qty_in or 0.0, "qty_out": qty_out or 0.0, "reason": reason,
                "partner_name": cell.get("partner") or False, "note": cell.get("note") or False,
            })
        lines = self.env["odin.stock.line"].create(vals_list)
        return {"created": len(lines), "ids": lines.ids, "skipped": skipped, "notes": notes,
                "months": sorted({(d.year, d.month) for d in lines.mapped("date")})}

    # ------------------------------------------------------------ stock sheet
    @api.model
    def _move_totals(self, wh, start, end):
        """Quantities into and out of the warehouse stock per product, done
        between start (included) and end (excluded), both UTC datetimes.
        What was really moved (the move lines, in the product's unit), and
        transfers to and from another warehouse count; moves between shelves
        of this one do not."""
        MoveLine = self.env["stock.move.line"]
        locations = self.env["stock.location"].with_context(active_test=False).search(
            [("id", "child_of", wh.lot_stock_id.id)]).ids
        base = [("state", "=", "done"), ("company_id", "=", wh.company_id.id)]
        if start:
            base.append(("date", ">=", start))
        if end:
            base.append(("date", "<", end))
        ins = MoveLine._read_group(base + [("location_dest_id", "in", locations), ("location_id", "not in", locations)],
                                   ["product_id"], ["quantity_product_uom:sum"])
        outs = MoveLine._read_group(base + [("location_id", "in", locations), ("location_dest_id", "not in", locations)],
                                    ["product_id"], ["quantity_product_uom:sum"])
        return {p.id: q for p, q in ins}, {p.id: q for p, q in outs}

    @api.model
    def get_sheet(self, year=False, month=False, group=False, search="", warehouse_id=False):
        wh = self._warehouse(warehouse_id)
        today = fields.Date.context_today(self)
        year, month = year or today.year, month or today.month
        start = date(year, month, 1)
        end = start + relativedelta(months=1)
        domain = [("is_storable", "=", True)]
        for word in (search or "").split():
            domain += ["|", ("default_code", "ilike", word), ("name", "ilike", word)]
        products = self.env["product.product"].search(domain, order="categ_id, default_code, name")
        now_qty = self.env["odin.stock.line"]._available(products, wh)
        ins, outs = self._move_totals(wh, self._utc(start), self._utc(end))
        later_in, later_out = self._move_totals(wh, self._utc(end), False)
        recent_out = self._move_totals(wh, self._utc(today - timedelta(days=28)), False)[1]
        groups = {}
        total = 0.0
        for p in products:
            closing = now_qty[p.id] - later_in.get(p.id, 0.0) + later_out.get(p.id, 0.0)
            added, removed = ins.get(p.id, 0.0), outs.get(p.id, 0.0)
            opening = closing - added + removed
            weekly = recent_out.get(p.id, 0.0) / 4
            cost = p.with_company(wh.company_id).standard_price
            row = {
                "product": self._product(p), "uom": p.uom_id.name, "opening": opening, "added": added,
                "removed": removed, "closing": closing, "cost": cost, "value": closing * cost,
                "cover": min(round(closing / weekly, 1), 52.0) if weekly > 0 and closing > 0 else None,
            }
            if group == "out" and closing > 0:
                continue
            if group == "moved" and not (added or removed):
                continue
            category = p.categ_id.complete_name or _("Other")
            if group and group not in ("out", "moved") and not category.lower().startswith(group.lower()):
                continue
            bucket = groups.setdefault(category, {"name": category, "rows": [], "value": 0.0})
            bucket["rows"].append(row)
            bucket["value"] += row["value"]
            total += row["value"]
        return {"year": year, "month": month, "label": f"{MONTHS[month - 1]} {year}",
                "months": [{"month": i, "label": MONTHS[i - 1]} for i in range(1, 13)],
                "warehouse": {"id": wh.id, "name": wh.name},
                "warehouses": [{"id": w.id, "name": w.name} for w in self._warehouses()],
                "groups": list(groups.values()), "total": total, "currency": self.env.company.currency_id.symbol}

    # ------------------------------------------------------------ count
    # A count is what is on the shelf in the whole warehouse stock. It is
    # kept on the quant of the stock location itself; stock on shelves below
    # it (sub-locations) is taken off, so the warehouse ends up at the count.
    @api.model
    def _count_quants(self, wh, products=None):
        domain = [("location_id", "=", wh.lot_stock_id.id), ("lot_id", "=", False), ("package_id", "=", False),
                  ("owner_id", "=", False)]
        if products is not None:
            domain.append(("product_id", "in", products.ids))
        return self.env["stock.quant"].search(domain)

    @api.model
    def get_count(self, group=False, search="", warehouse_id=False, limit=400):
        wh = self._warehouse(warehouse_id)
        domain = [("is_storable", "=", True)]
        for word in (search or "").split():
            domain += ["|", ("default_code", "ilike", word), ("name", "ilike", word)]
        if group and group != "counted":
            domain.append(("categ_id.complete_name", "=ilike", f"{_escape_like(group)}%"))
        all_quants = self._count_quants(wh).filtered("inventory_quantity_set")
        if group == "counted":
            domain.append(("id", "in", all_quants.product_id.ids))
        Product = self.env["product.product"]
        total = Product.search_count(domain)
        products = Product.search(domain, order="categ_id, default_code, name", limit=limit)
        available = self.env["odin.stock.line"]._available(products, wh)
        quants = {q.product_id.id: q for q in all_quants}
        rows = []
        for p in products:
            system = available[p.id]
            quant = quants.get(p.id)
            got = quant.inventory_quantity + (system - quant.quantity) if quant else None
            cost = p.with_company(wh.company_id).standard_price
            rows.append({"product": self._product(p), "uom": p.uom_id.name, "category": p.categ_id.complete_name,
                         "system": system, "counted": got, "tracked": p.tracking != "none",
                         "moved": bool(quant and quant.is_outdated),
                         "difference": None if got is None else got - system,
                         "value": None if got is None else (got - system) * cost})
        diffs = [r for r in rows if r["difference"]]
        return {"rows": rows, "counted": len(all_quants), "differences": len(diffs), "total": total,
                "shown": len(rows), "moved": len(all_quants.filtered("is_outdated")),
                "difference_value": sum(r["value"] for r in diffs), "can_post": self._is_supervisor(),
                "currency": self.env.company.currency_id.symbol,
                "warehouse": {"id": wh.id, "name": wh.name},
                "warehouses": [{"id": w.id, "name": w.name} for w in self._warehouses()]}

    @api.model
    def count_set(self, product_id, qty, warehouse_id=False):
        """Write what is on the shelf; ``qty`` None clears the count."""
        wh = self._warehouse(warehouse_id)
        product = self.env["product.product"].browse(product_id)
        if product.tracking != "none":
            raise UserError(_("%s is tracked by lot or serial number: count it in Inventory > Physical Inventory, "
                              "lot by lot.", product.display_name))
        Quant = self.env["stock.quant"].with_context(inventory_mode=True)
        quant = self._count_quants(wh, product)[:1].with_context(inventory_mode=True)
        if qty is None or qty == "":
            if quant:
                quant.action_clear_inventory_quantity()
            return True
        elsewhere = self.env["odin.stock.line"]._available(product, wh)[product.id] - quant.quantity
        target = float(qty) - elsewhere
        if quant:
            quant.inventory_quantity = target
        else:
            Quant.create({"product_id": product.id, "location_id": wh.lot_stock_id.id, "inventory_quantity": target})
        return True

    @api.model
    def count_post(self, warehouse_id=False, resolve=False):
        """Apply every count as one adjustment. When stock moved after an item
        was counted, Odoo cannot tell whether the count saw that movement:
        nothing is applied until the supervisor says which to keep
        (``resolve``): "counted" sets the item to the number counted,
        "difference" applies the difference found when it was counted."""
        if not self._is_supervisor():
            raise UserError(_("Only a Stock Book supervisor can post a count."))
        wh = self._warehouse(warehouse_id)
        quants = self._count_quants(wh).filtered("inventory_quantity_set")
        if not quants:
            return {"applied": 0}
        moved = quants.filtered("is_outdated")
        if moved and resolve not in ("counted", "difference"):
            return {"applied": 0, "moved": [{
                "product": self._product(q.product_id), "counted": q.inventory_quantity,
                "then": q.inventory_quantity - q.inventory_diff_quantity, "now": q.quantity,
                "uom": q.product_id.uom_id.name} for q in moved]}
        for quant in moved:
            if resolve == "difference":
                quant.inventory_quantity = quant.quantity + quant.inventory_diff_quantity
            else:
                quant.inventory_diff_quantity = quant.inventory_quantity - quant.quantity
        # "Moved since counted" is worked out from the difference, which it
        # does not list as what it depends on.
        moved.invalidate_recordset(["is_outdated"])
        name = _("Stock Book count %s", fields.Date.to_string(fields.Date.context_today(self)))
        result = quants.with_context(inventory_name=name).action_apply_inventory()
        if isinstance(result, dict):
            raise UserError(_("Odoo asked a question while posting the count (%s). Post it from Inventory > "
                              "Physical Inventory instead.", result.get("name") or result.get("res_model")))
        return {"applied": len(quants)}
