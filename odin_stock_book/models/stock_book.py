import re
from datetime import date, datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
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
                "display_name": product.display_name, "uom": product.uom_id.name,
                "made": self._make_reason(product)} if product else False

    @api.model
    def _make_reason(self, product):
        """How an item with its own recipe is made here; False if it is bought."""
        if not product.product_tmpl_id.bom_ids:
            return False
        return "production" if "bulk" in (product.categ_id.complete_name or "").lower() else "filling"

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
            # As the cards count them: a short line is under Short, not Needs.
            keep = {"need": NEED_KEYS, "ready": {"ready"}, "posted": {"posted"}}.get(status, {status})
            rows = [r for r in rows if r["status_key"] in keep]
        if search:
            needle = search.lower()
            rows = [r for r in rows if needle in " ".join(filter(None, [
                r["ref"], r["product"] and r["product"]["display_name"], r["partner_name"], r["note"],
                r["purchase_ref"], r["purchase"],
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
            "to_bill": self._orders_to_bill(),
            "can_open_bill": self.env.user.has_group("account.group_account_invoice")
            or self.env.user.has_group("account.group_account_readonly"),
        }

    @api.model
    def _orders_to_bill(self):
        """Received but not billed: the stock is not in the accounts until
        they are. Shown to those who can open them."""
        if not self.env.user.has_group("purchase.group_purchase_user"):
            return 0
        return self.env["purchase.order"].search_count(
            [("company_id", "=", self.env.company.id), ("invoice_status", "=", "to invoice")])

    @api.model
    def _row(self, line, status, onhand, comp_onhand, reversed_by=None):
        key, label, tone, detail = status
        recipes = line._recipes() if line.reason in MAKES and line.state == "draft" else line.bom_id
        order = line.purchase_id.sudo()
        # Billed in the window, or from Purchases afterwards.
        bill = order.invoice_ids.filtered(lambda m: m.state == "posted")[:1] or order.invoice_ids[:1]
        sale = line.stock_sale_id.sudo()
        bill = bill or sale.invoice_id
        undone = line.stock_purchase_id.sudo().undone_on
        return {
            "id": line.id, "date": fields.Date.to_string(line.date), "ref": line.ref or "",
            "reason": line.reason or "", "reason_label": REASONS[line.reason][1] if line.reason else "",
            "posts_as": _("Purchase order, receipt, bill and payment") if line.stock_purchase_id.pay_journal_id
            else _("Purchase order, receipt and bill") if line.stock_purchase_id
            else _("Sales order, delivery, invoice and payment") if sale.pay_journal_id
            else _("Sales order, delivery and invoice") if sale
            else REASONS[line.reason][2] if line.reason else "",
            "window": bool(line.stock_purchase_id or line.stock_sale_id),
            "undone": bool(undone),
            "can_undo": bool(line.state == "posted" and line.stock_purchase_id.order_id and not undone
                             and self._is_supervisor()),
            "unit_price": line.unit_price,
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
            "purchase": line.purchase_id.sudo().name or "", "current_cost": line.product_id.standard_price,
            "purchase_ref": line.purchase_ref or "", "tax_name": line.tax_id.name or "",
            "bill": bill.name or "", "bill_id": bill.id or False,
            "unbilled": order.invoice_status == "to invoice" and not undone,
            "paid_from": (line.stock_purchase_id.pay_journal_id or line.stock_sale_id.pay_journal_id).sudo().name or "",
            "note_print": " · ".join(filter(None, [line.purchase_ref and _("LPO %s", line.purchase_ref), line.note])),
            "reverses": line.reverses_id.ref or "", "reversed_by": reversed_by or "",
            "can_reverse": line._can_reverse() and not reversed_by,
        }

    @api.model
    def _out_in_recipes(self, wh):
        """Components the recipes need that are at zero or below: what stops
        the next filling or batch."""
        out, recipes = self._recipe_shortages(wh)
        blocked = {bom for product in out for bom in recipes[product.id]}
        return {"count": len(out), "recipes": len(blocked), "examples": out[:3].mapped("name")}

    @api.model
    def _recipe_shortages(self, wh):
        """The items at zero or below that a live recipe uses, those most
        recipes wait for first, with those recipes by item id. Archived items
        and recipes of archived products stop no batch and are left out."""
        lines = self.env["mrp.bom.line"].search([("bom_id.active", "=", True)]).filtered(
            lambda l: l.product_id.active and l.product_id.is_storable and l.bom_id.product_tmpl_id.active)
        available = self.env["odin.stock.line"]._available(lines.product_id, wh)
        recipes = {}
        for line in lines:
            product = line.product_id
            if available[product.id] <= 0 and not product.stock_book_unlimited:
                recipes.setdefault(product.id, self.env["mrp.bom"])
                recipes[product.id] |= line.bom_id
        out = self.env["product.product"].browse(list(recipes)).sorted(
            lambda p: (-len(recipes[p.id]), p.default_code or p.name))
        return out, recipes

    @api.model
    def get_recipes(self, group=False, search="", warehouse_id=False):
        """What the Out for recipes card opens: each item that stops a batch,
        which recipes wait for it, how fast it goes, where it last came from,
        and whether it is bought or made here."""
        wh = self._warehouse(warehouse_id)
        out, recipes = self._recipe_shortages(wh)
        total_items, blocked = len(out), {bom for boms in recipes.values() for bom in boms}
        for word in (search or "").lower().split():
            out = out.filtered(lambda p: word in (p.default_code or "").lower() or word in p.name.lower())
        if group == "made":
            out = out.filtered(self._make_reason)
        elif group:
            out = out.filtered(lambda p: (p.categ_id.complete_name or "").lower().startswith(group.lower()))
        today = fields.Date.context_today(self)
        recent = self._move_totals(wh, self._utc(today - timedelta(days=28)), self._utc(today + timedelta(days=1)))[1]
        available = self.env["odin.stock.line"]._available(out, wh)
        Order = self.env["purchase.order.line"].sudo()
        rows = []
        for product in out:
            last = Order.search([("product_id", "=", product.id), ("state", "=", "purchase"),
                                 ("company_id", "=", wh.company_id.id)], order="date_order desc, id desc", limit=1)
            boms = recipes[product.id]
            make = self._make_reason(product)
            rows.append({
                "product": {**self._product(product), "stock": True, "tracked": product.tracking != "none",
                            "onhand": available[product.id]},
                "uom": product.uom_id.name, "onhand": available[product.id], "made": bool(make),
                "reason": make or "received",
                "category": product.categ_id.complete_name or "",
                "recipe_count": len(boms), "recipes": [b.product_tmpl_id.name for b in boms[:40]],
                "weekly": round(recent.get(product.id, 0.0) / 4, 2),
                "last": {"date": fields.Date.to_string(last.date_order.date()), "partner": last.partner_id.name,
                         "cost": last.price_unit} if last else False,
            })
        return {
            "warehouse": {"id": wh.id, "name": wh.name},
            "warehouses": [{"id": w.id, "name": w.name} for w in self._warehouses()],
            "rows": rows, "items": total_items, "recipes": len(blocked),
            "currency": self.env.company.currency_id.symbol,
        }

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
        """Post the lines one after another, in date order, so a receipt lets
        the use after it through, as the page projected. One that cannot go
        stays a draft and is reported; the others are not held back by it."""
        if not self._is_supervisor():
            raise UserError(_("Only a Stock Book supervisor can post. Ask a supervisor to post it."))
        lines = self.env["odin.stock.line"].browse(ids).exists().filtered(lambda l: l.state == "draft")
        lines |= lines._purchase_siblings() | lines.stock_sale_id.line_ids.filtered(lambda l: l.state == "draft")
        skipped, failed = [], []
        for line in lines.sorted(lambda l: (l.date, l.id)):
            if line.state != "draft":
                continue  # went with its order
            key, label, _tone, detail = line._status()
            if key != "ready" and not (key == "short" and allow_short):
                skipped.append({"id": line.id, "item": line.product_id.name or _("Line"), "why": label})
                continue
            batch = line
            if line.reason == "received":
                purchase = line._purchase_key()
                batch = lines.filtered(lambda l: l.state == "draft" and l.reason == "received"
                                       and l._purchase_key() == purchase and l._status()[0] == "ready") | line
            elif line.stock_sale_id:
                batch = lines.filtered(lambda l: l.state == "draft" and l.stock_sale_id == line.stock_sale_id)
            try:
                with self.env.cr.savepoint():
                    batch.action_post(allow_short=allow_short)
            except UserError as error:
                self.env.invalidate_all()
                failed += [{"id": l.id, "item": l.product_id.name or _("Line"), "why": error.args[0]} for l in batch]
        posted = lines.filtered(lambda l: l.state == "posted").sorted("ref")
        return {"count": len(posted), "refs": posted.mapped("ref"), "ids": posted.ids,
                "skipped": skipped, "failed": failed}

    # ------------------------------------------------------------ add purchase
    @api.model
    def _purchase_taxes(self):
        """Purchase VAT to choose from. Withholding is left out: it is taken
        when paying, on top of the VAT, not instead of it."""
        taxes = self.env["account.tax"].search([
            ("type_tax_use", "=", "purchase"), ("company_id", "=", self.env.company.id),
            ("amount", ">=", 0)], order="sequence, id")
        # A VAT with no account would land on the Stock account with the goods.
        return taxes.filtered(lambda t: not t.amount or t.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == "tax").account_id)

    @api.model
    def _purchase_pay_journals(self):
        """Journals a purchase can be paid from: those whose payments reach the
        ledger. A float kept by the Petty Cash book has no outstanding account:
        what it pays for goes through that book, which receives the stock itself."""
        journals = self.env["account.journal"].sudo().search(
            [("company_id", "=", self.env.company.id), ("type", "in", ("bank", "cash"))], order="sequence, id")
        return journals.filtered(lambda j: j.outbound_payment_method_line_ids[:1].payment_account_id)

    @api.model
    def book_purchase_options(self):
        taxes = self._purchase_taxes()
        journals = self._purchase_pay_journals()
        names = journals.mapped("name")
        return {
            # Two journals of one name are told apart by their code.
            "journals": [{"id": j.id, "label": f"{j.name} ({j.code})" if names.count(j.name) > 1 else j.name}
                         for j in journals],
            "taxes": [{"id": t.id, "label": _("%s (incl.)", t.name) if t.price_include and t.amount else t.name}
                      for t in taxes],
            "today": fields.Date.to_string(fields.Date.context_today(self)),
            "can_post": self._is_supervisor(), "currency": self.env.company.currency_id.name,
        }

    @api.model
    def book_purchase_suppliers(self, text):
        """Contacts for the Supplier box, suppliers first, and whether the
        words typed are a new one: a supplier is picked, never guessed."""
        name = " ".join((text or "").split())
        if not name:
            return {"items": [], "can_create": False}
        partners = self.env["res.partner"].search([("name", "ilike", name), ("parent_id", "=", False)],
                                                  order="supplier_rank desc, name", limit=8)
        exact = partners.filtered(lambda p: p.name.casefold() == name.casefold())
        return {"items": [{"id": p.id, "name": p.name} for p in partners], "can_create": not exact}

    @api.model
    def book_purchase_products(self, text, warehouse_id=False):
        """Items for a purchase line: stock first, then what is bought but not
        stocked (transport, labour), which goes on the bill only."""
        result = [{**item, "stock": True} for item in self.book_suggest_products(text, warehouse_id)]
        domain = [("is_storable", "=", False), ("purchase_ok", "=", True)]
        for word in (text or "").split():
            domain += ["|", ("default_code", "ilike", word), ("name", "ilike", word)]
        if (text or "").split():
            result += [{**self._product(p), "stock": False, "tracked": False, "onhand": 0.0}
                       for p in self.env["product.product"].search(domain, limit=4, order="name")]
        return result

    @api.model
    def book_purchase_item(self, product_id, partner_id=False):
        """What a new line starts with: the usual VAT, and the last price as
        a hint. Never typed in for the user: the cost comes from the paper."""
        product = self.env["product.product"].browse(product_id).exists()
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        Line = self.env["odin.stock.line"]
        # Never one included in the price unless chosen on the line: the cost
        # is typed before VAT, however the item's own default reads.
        tax = Line._net_purchase_tax(Line._usual_purchase_tax(product, partner, self.env.company), self.env.company)
        if tax not in self._purchase_taxes():
            tax = self.env["account.tax"]
        last = self.env["purchase.order.line"].sudo().search(
            [("company_id", "=", self.env.company.id), ("state", "=", "purchase"), ("product_id", "=", product.id)]
            + ([("partner_id", "=", partner.id)] if partner else []), order="id desc", limit=1)
        return {"tax_id": tax.id or False, "last_cost": last.price_unit or product.standard_price}

    @api.model
    def book_purchase_check(self, partner_id, ref, ignore_ids=None):
        """Why this number cannot be used, as soon as it is typed; False if it
        can. ``ignore_ids``: the lines being edited, whose number it may be."""
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        try:
            self.env["odin.stock.line"]._check_purchase_ref(partner, ref, self.env.company)
            self._check_purchase_waiting(partner, ref, ignore_ids)
        except UserError as error:
            return str(error)
        return False

    @api.model
    def _check_purchase_waiting(self, partner, ref, ignore_ids=None):
        ref = " ".join((ref or "").split())
        waiting = self.env["odin.stock.line"].search(
            [("state", "=", "draft"), ("reason", "=", "received"), ("company_id", "=", self.env.company.id),
             ("partner_id", "=", partner.id), ("purchase_ref", "=ilike", _escape_like(ref)),
             ("id", "not in", ignore_ids or [])], limit=1)
        if waiting:
            raise UserError(_("%(ref)s from %(supplier)s is already on the book, waiting to be posted.",
                              ref=ref, supplier=partner.display_name))

    @api.model
    def _purchase_tax_lines(self, rows, partner):
        Tax = self.env["account.tax"]
        Product = self.env["product.product"]
        company = self.env.company
        allowed = self._purchase_taxes()
        base_lines = []
        for row in rows:
            tax = allowed.filtered(lambda t: t.id == row.get("tax_id"))
            base_lines.append(Tax._prepare_base_line_for_taxes_computation(
                None, price_unit=row.get("cost") or 0.0, quantity=row.get("qty") or 0.0, tax_ids=tax,
                product_id=Product.browse(row.get("product_id") or []).exists(), partner_id=partner,
                currency_id=company.currency_id, rate=1.0,
            ))
        Tax._add_tax_details_in_base_lines(base_lines, company)
        Tax._round_base_lines_tax_details(base_lines, company)
        return base_lines

    @api.model
    def book_purchase_totals(self, rows, partner_id=False):
        """Subtotal, VAT and total by Odoo's own tax computation, the same
        as the purchase order will show."""
        partner = self.env["res.partner"].browse(partner_id or []).exists()
        base_lines = self._purchase_tax_lines(rows, partner)
        summary = self.env["account.tax"]._get_tax_totals_summary(
            base_lines=base_lines, currency=self.env.company.currency_id, company=self.env.company)
        return {
            "lines": [line["tax_details"]["total_excluded_currency"] for line in base_lines],
            "subtotal": summary["base_amount_currency"], "vat": summary["tax_amount_currency"],
            "total": summary["total_amount_currency"],
        }

    @api.model
    def book_purchase_load(self, line_id):
        """A draft line for the purchase window: all of its LPO if it came from
        the window, only itself otherwise. Saving replaces what is loaded."""
        Line = self.env["odin.stock.line"]
        line = Line.browse(line_id).exists()
        if not line or line.state != "draft" or line.reason != "received":
            raise UserError(_("Only a received line not yet posted can be opened as a purchase."))
        window = line.stock_purchase_id
        lines = window.line_ids.filtered(lambda l: l.state == "draft") if window else line
        partner = window.partner_id or line.partner_id
        name = (line.partner_name or "").strip()
        if not partner and name:
            found = self.env["res.partner"].search([("name", "=ilike", _escape_like(name)), ("parent_id", "=", False)],
                                                   limit=1)
            partner = found
        rows = [{"product": {**self._product(l.product_id), "stock": True, "tracked": l.product_id.tracking != "none"},
                 "qty": l.qty_in, "cost": l.unit_cost, "tax_id": l.tax_id.id or False, "note": l.note or ""} for l in lines]
        rows += [{"product": {**self._product(c.product_id), "stock": False, "tracked": False}, "qty": c.qty,
                  "cost": c.unit_cost, "tax_id": c.tax_id.id or False, "note": c.name or ""} for c in window.charge_ids]
        return {
            "supplier": {"id": partner.id, "name": partner.name} if partner else False,
            "supplier_query": partner.name if partner else name.title(),
            "purchase_ref": line.purchase_ref or "", "date": fields.Date.to_string(line.date),
            "paid": (str(window.pay_journal_id.id) if window.pay_journal_id else "none") if window else "",
            "lines": rows, "replace_ids": lines.ids, "edit": bool(window),
        }

    @api.model
    def _purchase_replace(self, ids):
        """What a purchase being saved takes the place of: its draft lines, and
        the entry they came from with its other costs."""
        lines = self.env["odin.stock.line"].browse(ids or []).exists()
        if lines.filtered(lambda l: l.state != "draft" or l.reason != "received"):
            raise UserError(_("These lines were posted meanwhile. Nothing was saved: check the book."))
        windows = lines.stock_purchase_id
        everything = lines | windows.line_ids
        if everything.filtered(lambda l: l.state == "posted"):
            raise UserError(_("These lines were posted meanwhile. Nothing was saved: check the book."))
        everything.unlink()
        windows.sudo().unlink()

    @api.model
    def book_purchase_save(self, values, post=True, replace_ids=None):
        """One LPO from the purchase window, as Stock Book lines: posted at
        once by a supervisor, left for one otherwise. All of it or none."""
        Line = self.env["odin.stock.line"]
        company = self.env.company
        self._purchase_replace(replace_ids)
        wh = self._warehouse(values.get("warehouse_id"))
        if not wh:
            raise UserError(_("Create a warehouse first."))
        ref = " ".join((values.get("purchase_ref") or "").split())
        if not ref:
            raise UserError(_("Type the LPO or invoice number."))
        partner = self.env["res.partner"].browse(values.get("partner_id") or []).exists()
        name = " ".join((values.get("partner_name") or "").split())
        if not partner and not name:
            raise UserError(_("Pick the supplier."))
        day = fields.Date.to_date(values.get("date"))
        if not day:
            raise UserError(_("Type the date."))
        if day > fields.Date.context_today(self):
            raise UserError(_("The date received cannot be in the future: %s.", day.strftime("%d %b %Y")))
        rows = values.get("lines") or []
        if not rows:
            raise UserError(_("Add at least one item."))
        allowed = self._purchase_taxes()
        journal = self.env["account.journal"].sudo().browse(values.get("pay_journal_id") or []).exists()
        if journal and journal not in self._purchase_pay_journals():
            raise UserError(_("Choose where it was paid from."))
        vals_list = []
        charges = []
        for number, row in enumerate(rows, 1):
            product = self.env["product.product"].browse(row.get("product_id") or []).exists()
            if not product or not (product.is_storable or product.purchase_ok):
                raise UserError(_("Line %s: pick an item that can be bought.", number))
            if product.tracking != "none":
                raise UserError(_("%s is tracked by lot or serial number. Receive it in Purchases, where the numbers "
                                  "are entered.", product.display_name))
            if (row.get("qty") or 0) <= 0:
                raise UserError(_("%s: type the quantity.", product.display_name))
            if (row.get("cost") or 0) <= 0:
                raise UserError(_("%s: type the cost per unit.", product.display_name))
            if row.get("tax_id") not in allowed.ids:
                raise UserError(_("%s: choose the VAT.", product.display_name))
            if not product.is_storable:
                charges.append(Command.create({
                    "product_id": product.id, "name": (row.get("note") or "").strip() or False, "qty": row["qty"],
                    "unit_cost": row["cost"], "tax_id": row["tax_id"],
                }))
                continue
            vals_list.append({
                "company_id": company.id, "warehouse_id": wh.id, "date": day, "reason": "received",
                "product_id": product.id, "qty_in": row["qty"], "unit_cost": row["cost"],
                "tax_id": row["tax_id"],
                "purchase_ref": ref, "note": row.get("note") or False,
            })
        if not vals_list:
            raise UserError(_("Add at least one item kept in stock. A purchase with no stock in it is a bill: enter "
                              "it in Accounting."))
        if not partner:
            # Asked for by name in the window: as typed. Sudo as the book does
            # when posting: supervisors need not create contacts in general.
            Partner = self.env["res.partner"]
            partner = Partner.search([("name", "=ilike", _escape_like(name)), ("parent_id", "=", False)], limit=1) \
                or Partner.sudo().create({"name": name, "company_id": False, "supplier_rank": 1}).sudo(False)
        Line._lock_purchase_supplier(partner)
        Line._check_purchase_ref(partner, ref, company)
        self._check_purchase_waiting(partner, ref)
        window = self.env["odin.stock.purchase"].create({
            "company_id": company.id, "warehouse_id": wh.id, "partner_id": partner.id, "purchase_ref": ref,
            "date": day, "pay_journal_id": journal.id or False, "charge_ids": charges,
        })
        for vals in vals_list:
            vals.update(partner_id=partner.id, partner_name=partner.name, stock_purchase_id=window.id)
        lines = Line.with_context(odin_stock_book_posting=True).create(vals_list).with_context(
            odin_stock_book_posting=False)
        posted = bool(post and self._is_supervisor())
        if posted:
            lines.action_post()
        order = lines.purchase_id[:1].sudo()
        bill = window.sudo().bill_id
        return {
            "bill": bill.name or "", "payment_state": bill.payment_state or "",
            "paid_from": journal.name or "",
            "ids": lines.ids, "refs": lines.mapped("ref") if posted else [], "posted": posted,
            "order": order.name or "", "order_id": order.id or False, "total": order.amount_total,
            "purchase_ref": ref, "date": fields.Date.to_string(day),
        }

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

    # ------------------------------------------------------------ undo a purchase
    @api.model
    def book_purchase_undo(self, line_id):
        """A saved purchase taken back in one step, and what it was, so it can
        be entered again correctly."""
        if not self._is_supervisor():
            raise UserError(_("Only a Stock Book supervisor can undo a purchase."))
        line = self.env["odin.stock.line"].browse(line_id).exists()
        window = line.stock_purchase_id
        if not window:
            raise UserError(_("Only a purchase made with Add purchase can be undone here."))
        posted = window.line_ids.filtered(lambda l: l.state == "posted")
        rows = [{"product": {**self._product(l.product_id), "stock": True, "tracked": False}, "qty": l.qty_in,
                 "cost": l.unit_cost, "tax_id": l.tax_id.id or False, "note": l.note or ""} for l in posted]
        rows += [{"product": {**self._product(c.product_id), "stock": False, "tracked": False}, "qty": c.qty,
                  "cost": c.unit_cost, "tax_id": c.tax_id.id or False, "note": c.name or ""} for c in window.charge_ids]
        again = {
            "supplier": {"id": window.partner_id.id, "name": window.partner_id.name},
            "supplier_query": window.partner_id.name, "purchase_ref": window.purchase_ref,
            "date": fields.Date.to_string(window.date),
            "paid": str(window.pay_journal_id.id) if window.pay_journal_id else "none",
            "lines": rows, "replace_ids": [], "edit": False,
        }
        order = window.order_id.sudo().name
        window._undo()
        return {"order": order, "purchase_ref": window.purchase_ref, "again": again}
