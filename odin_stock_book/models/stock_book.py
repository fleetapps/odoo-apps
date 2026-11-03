from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .stock_book_line import MAKES, REASONS

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
PRODUCT_FIELDS = ["id", "display_name", "default_code", "name"]


class StockBook(models.AbstractModel):
    """What the Stock Book page reads and writes. One place, so the page
    makes few calls and every number comes from the same rules."""

    _name = "odin.stock.book"
    _description = "Stock Book"

    # ------------------------------------------------------------ helpers
    @api.model
    def _warehouse(self, warehouse_id=False):
        Warehouse = self.env["stock.warehouse"]
        return (Warehouse.browse(warehouse_id) if warehouse_id else Warehouse).search(
            [("company_id", "=", self.env.company.id)] + ([("id", "=", warehouse_id)] if warehouse_id else []),
            limit=1)

    @api.model
    def _product(self, product):
        return {"id": product.id, "code": product.default_code or "", "name": product.name,
                "display_name": product.display_name, "uom": product.uom_id.name} if product else False

    @api.model
    def _is_supervisor(self):
        return self.env.user.has_group("odin_stock_book.group_stock_book_supervisor")

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
            rows.append(self._row(line, line_status, onhand, comp_onhand))
        if reason:
            groups = {"make": MAKES, "sale": {"sale", "return"}, "in": {"received"},
                      "out": {"spoiled", "sample", "used"}, "correction": {"correction"}}
            keep = groups.get(reason, {reason})
            rows = [r for r in rows if r["reason"] in keep]
        if status:
            keep = {"need": {"need_product", "need_reason", "need_qty", "wrong_way", "need_recipe", "need_partner",
                             "short"}, "ready": {"ready"}, "posted": {"posted"}}.get(status, {status})
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
        value = self.env["stock.quant"]._read_group(
            [("location_id", "child_of", wh.lot_stock_id.id)], [], ["value:sum"])[0][0] or 0.0
        return {
            "warehouse": {"id": wh.id, "name": wh.name},
            "year": year, "month": month, "today": fields.Date.to_string(today),
            "months": [{"month": i, "label": MONTHS[i - 1], "count": per_month[i]} for i in range(1, 13)],
            "rows": rows, "counts": counts,
            "drafts_total": len(all_drafts),
            "older_open": {"count": len(older), "year": older[:1].date.year if older else False,
                           "month": older[:1].date.month if older else False},
            "stock_value": value, "currency": self.env.company.currency_id.symbol,
            "out_in_recipes": self._out_in_recipes(wh),
            "reasons": [{"key": k, "label": v[1], "direction": v[0]} for k, v in REASONS.items()],
            "can_post": self._is_supervisor(),
        }

    @api.model
    def _row(self, line, status, onhand, comp_onhand):
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
                "unlimited": c.product_id.stock_book_unlimited,
            } for c in line.component_ids],
            "posted_by": line.posted_by_id.name or "", "has_document": bool(line.move_ids),
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
    def book_save(self, line_id, values):
        allowed = {"date", "reason", "product_id", "qty_in", "qty_out", "partner_name", "note", "unit_cost",
                   "bom_id", "warehouse_id"}
        values = {k: v for k, v in values.items() if k in allowed}
        Line = self.env["odin.stock.line"]
        line = Line.browse(line_id) if line_id else Line.create(values)
        if line_id:
            line.write(values)
        wh = line.warehouse_id
        status = line._status()
        return self._row(line, status, Line._available(line.product_id, wh).get(line.product_id.id, 0.0), {})

    @api.model
    def book_component(self, line_id, component_id=False, product_id=False, qty=None, remove=False):
        Component = self.env["odin.stock.line.component"]
        line = self.env["odin.stock.line"].browse(line_id)
        if line.state != "draft":
            raise UserError(_("A posted line cannot be changed. Add a Correction line instead."))
        if remove and component_id:
            Component.browse(component_id).unlink()
        elif component_id:
            Component.browse(component_id).write({"qty": max(qty or 0.0, 0.0)})
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
        return len(lines)

    @api.model
    def book_cancel(self, ids):
        self.env["odin.stock.line"].browse(ids).action_cancel()
        return True

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
        exact = Product.search([("is_storable", "=", True), ("default_code", "=ilike", text.strip())], limit=1)
        products = exact | Product.search(domain, limit=8, order="default_code")
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
                           "usual_reason": usual})
        return result

    @api.model
    def book_suggest_partners(self, text):
        if not text or len(text) < 2:
            return []
        self.env.cr.execute("""
            SELECT partner_name, COUNT(*) FROM odin_stock_line
             WHERE partner_name ILIKE %s GROUP BY partner_name ORDER BY COUNT(*) DESC LIMIT 6""", (f"%{text}%",))
        names = [row[0] for row in self.env.cr.fetchall()]
        for partner in self.env["res.partner"].search([("name", "ilike", text), ("parent_id", "=", False)], limit=6):
            if partner.name.upper() not in names:
                names.append(partner.name.upper())
        return names[:8]

    # ------------------------------------------------------------ stock sheet
    @api.model
    def _move_totals(self, wh, start, end):
        """Quantities into and out of the warehouse stock per product, done
        between start (included) and end (excluded)."""
        Move = self.env["stock.move"]
        base = [("state", "=", "done"), ("company_id", "=", wh.company_id.id)]
        if start:
            base.append(("date", ">=", fields.Datetime.to_datetime(start)))
        if end:
            base.append(("date", "<", fields.Datetime.to_datetime(end)))
        stock = wh.lot_stock_id
        ins = Move._read_group(base + [("location_dest_id", "child_of", stock.id),
                                       ("location_id.usage", "!=", "internal")], ["product_id"], ["product_qty:sum"])
        outs = Move._read_group(base + [("location_id", "child_of", stock.id),
                                        ("location_dest_id.usage", "!=", "internal")], ["product_id"], ["product_qty:sum"])
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
        ins, outs = self._move_totals(wh, start, end)
        later_in, later_out = self._move_totals(wh, end, False)
        recent_out = self._move_totals(wh, today - timedelta(days=28), today + timedelta(days=1))[1]
        groups = {}
        total = 0.0
        for p in products:
            closing = now_qty[p.id] - later_in.get(p.id, 0.0) + later_out.get(p.id, 0.0)
            added, removed = ins.get(p.id, 0.0), outs.get(p.id, 0.0)
            opening = closing - added + removed
            weekly = recent_out.get(p.id, 0.0) / 4
            row = {
                "product": self._product(p), "uom": p.uom_id.name, "opening": opening, "added": added,
                "removed": removed, "closing": closing, "cost": p.standard_price, "value": closing * p.standard_price,
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
                "groups": list(groups.values()), "total": total, "currency": self.env.company.currency_id.symbol}

    # ------------------------------------------------------------ count
    @api.model
    def get_count(self, group=False, search="", warehouse_id=False):
        wh = self._warehouse(warehouse_id)
        domain = [("is_storable", "=", True)]
        for word in (search or "").split():
            domain += ["|", ("default_code", "ilike", word), ("name", "ilike", word)]
        if group and group != "counted":
            domain.append(("categ_id.complete_name", "=ilike", f"{group}%"))
        products = self.env["product.product"].search(domain, order="categ_id, default_code, name", limit=400)
        available = self.env["odin.stock.line"]._available(products, wh)
        quants = self.env["stock.quant"].search([("product_id", "in", products.ids),
                                                 ("location_id", "=", wh.lot_stock_id.id),
                                                 ("inventory_quantity_set", "=", True)])
        counted = {q.product_id.id: q.inventory_quantity for q in quants}
        rows = []
        for p in products:
            if group == "counted" and p.id not in counted:
                continue
            system = available[p.id]
            got = counted.get(p.id)
            rows.append({"product": self._product(p), "uom": p.uom_id.name, "category": p.categ_id.complete_name,
                         "system": system, "counted": got,
                         "difference": None if got is None else got - system,
                         "value": None if got is None else (got - system) * p.standard_price})
        diffs = [r for r in rows if r["difference"]]
        return {"rows": rows, "counted": len(counted), "differences": len(diffs),
                "difference_value": sum(r["value"] for r in diffs), "can_post": self._is_supervisor(),
                "currency": self.env.company.currency_id.symbol}

    @api.model
    def count_set(self, product_id, qty, warehouse_id=False):
        """Write what is on the shelf; ``qty`` None clears the count."""
        wh = self._warehouse(warehouse_id)
        Quant = self.env["stock.quant"].with_context(inventory_mode=True)
        quant = Quant.search([("product_id", "=", product_id), ("location_id", "=", wh.lot_stock_id.id)], limit=1)
        if qty is None or qty == "":
            if quant:
                quant.action_clear_inventory_quantity()
            return True
        if quant:
            quant.inventory_quantity = float(qty)
        else:
            Quant.create({"product_id": product_id, "location_id": wh.lot_stock_id.id, "inventory_quantity": float(qty)})
        return True

    @api.model
    def count_post(self, warehouse_id=False):
        if not self._is_supervisor():
            raise UserError(_("Only a Stock Book supervisor can post a count."))
        wh = self._warehouse(warehouse_id)
        quants = self.env["stock.quant"].search([("location_id", "=", wh.lot_stock_id.id),
                                                 ("inventory_quantity_set", "=", True)])
        name = _("Stock Book count %s", fields.Date.to_string(fields.Date.context_today(self)))
        quants.with_context(inventory_name=name).action_apply_inventory()
        return len(quants)
