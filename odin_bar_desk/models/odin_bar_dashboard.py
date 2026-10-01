from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .odin_bar_desk import DAYS_BACK

PERIODS = (3, 7, 30)
TOP_PRODUCTS = 5
TOP_BELOW_PAR = 5
TO_CHASE = 8


class OdinBarDashboard(models.AbstractModel):
    """Figures for the Bar Control dashboard, in the order a manager reads them:
    is every trading day closed, how each location is doing, what was lost and
    why, what to reorder, and what left the club. All values are at cost.

    Read-only and for Bar Desk managers. Trading days run to yesterday: today
    is still trading and is closed tomorrow morning.
    """

    _name = "odin.bar.dashboard"
    _description = "Bar Control dashboard"

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    @api.model
    def dashboard_data(self, period=7, bar_id=False):
        """Everything the dashboard shows for the last ``period`` trading days,
        for one location (``bar_id``) or all of them."""
        self._check_manager()
        # Stock figures come from SQL views, which only see what is written.
        self.env.flush_all()
        period = int(period) if int(period or 0) in PERIODS else 7
        all_bars = self._bars()
        if not all_bars:
            return {"empty": True}
        bars = all_bars.filtered(lambda bar: bar.id == int(bar_id)) if bar_id else all_bars
        if not bars:
            raise UserError(_("Pick a location."))
        today = all_bars[0]._business_date()
        last = today - timedelta(days=1)
        days = [last - timedelta(days=back) for back in range(period - 1, -1, -1)]
        previous = (days[0] - timedelta(days=period), days[0] - timedelta(days=1))
        currency = self.env.company.currency_id
        return {
            "empty": False,
            "period": period,
            "bar_id": bars.id if bar_id else False,
            "currency": currency.symbol or currency.name,
            "today": fields.Date.to_string(today),
            "date_from": fields.Date.to_string(days[0]),
            "date_to": fields.Date.to_string(last),
            "bars": [self._bar_info(bar) for bar in all_bars],
            "closing": self._closing(all_bars, bars, days, today),
            "locations": self._locations(bars, days, previous, today),
            "variance": self._variance(bars, all_bars, days, previous),
            "stock": self._stock(bars, all_bars),
            "out": self._out_of_club(bars, days[0], today),
        }

    @api.model
    def dashboard_open(self, what, bar_id=False, date_from=False, date_to=False, record_id=False):
        """The list or report behind a figure, filtered to what was tapped."""
        self._check_manager()
        bars = self._bars()
        if bar_id:
            bars = bars.filtered(lambda bar: bar.id == int(bar_id))
        date_domain = []
        if date_from:
            date_domain.append(("business_date", ">=", date_from))
        if date_to:
            date_domain.append(("business_date", "<=", date_to))
        if what == "desk":
            action = self.env["ir.actions.actions"]._for_xml_id("odin_bar_desk.action_bar_desk_backend")
            action["context"] = {"bar_desk_day": date_from}
            return action
        if what == "count":
            count = self.env["odin.bar.count"].browse(int(record_id)).exists()
            if count:
                return {
                    "type": "ir.actions.act_window",
                    "res_model": "odin.bar.count",
                    "res_id": count.id,
                    "views": [(False, "form")],
                }
            action = self.env["ir.actions.actions"]._for_xml_id("odin_bar_desk.odin_bar_count_action")
            action["domain"] = [("bar_id", "in", bars.ids), ("kind", "=", "closing")] + date_domain
            return action
        if what == "variance":
            action = self.env["ir.actions.actions"]._for_xml_id("odin_bar_desk.odin_bar_count_line_variance_action")
            domain = [("bar_id", "in", bars.ids), ("state", "=", "approved")] + date_domain
            if record_id:
                domain.append(("variance_reason_id", "=", int(record_id)))
            action["domain"] = domain
            return action
        if what == "variance_product":
            action = self.env["ir.actions.actions"]._for_xml_id("odin_bar_desk.odin_bar_count_line_variance_action")
            action["domain"] = [
                ("bar_id", "in", bars.ids),
                ("state", "=", "approved"),
                ("product_id", "=", int(record_id)),
            ] + date_domain
            return action
        if what == "stock":
            action = self.env["ir.actions.actions"]._for_xml_id("odin_bar_desk.action_bar_stock_by_location")
            action["domain"] = [("bar_id", "in", bars.ids)]
            return action
        if what == "history":
            action = self.env["ir.actions.actions"]._for_xml_id("odin_bar_desk.action_bar_stock_history")
            domain = [("bar_id", "in", bars.ids)] + date_domain
            if record_id:
                domain.append(("kind", "=", record_id))
            action["domain"] = domain
            return action
        if what == "out":
            action = self.env["ir.actions.actions"]._for_xml_id("odin_bar_desk.action_bar_moves")
            domain = [("id", "in", self._out_pickings(bars, date_from, date_to).ids)]
            if record_id:
                domain.append(("bar_reason_id", "=", int(record_id)))
            action["domain"] = domain
            return action
        if what == "picking":
            return {
                "type": "ir.actions.act_window",
                "res_model": "stock.picking",
                "res_id": int(record_id),
                "views": [(False, "form")],
            }
        raise UserError(_("Nothing to open."))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @api.model
    def _check_manager(self):
        if not self.env.user.has_group("odin_bar_desk.group_bar_desk_manager"):
            raise AccessError(_("The Bar Control dashboard is for Bar Desk managers."))

    @api.model
    def _bars(self):
        """Locations of the current company, in Desk order: the store first."""
        bars = self.env["odin.bar"].sudo().search([("company_id", "=", self.env.company.id)])
        return bars.sorted(lambda bar: (bar.kind != "store", bar.sequence, bar.name, bar.id))

    @api.model
    def _bar_info(self, bar):
        return {"id": bar.id, "name": bar.name, "code": bar.code, "kind": bar.kind}

    @api.model
    def _day_label(self, day):
        return self.env["odin.bar.desk"]._desk_day_label(day)

    @api.model
    def _cost(self, product, qty):
        return qty * product.sudo().standard_price

    @api.model
    def _bulk(self, product, qty):
        """``qty`` (stock unit) in bottles or crates, as staff read it: "3 btl"."""
        template = product.product_tmpl_id
        bulk = template.bar_bulk_uom_id
        if not bulk:
            return f"{qty:g}"
        value = product.uom_id._compute_quantity(qty, bulk, round=False)
        if template.bar_bottle_uom_id:
            unit = "btl"
        elif bulk.name.lower().startswith("crate"):
            unit = "cr"
        else:
            unit = bulk.name
        return f"{round(value, 1):g} {unit}"

    @api.model
    def _counts(self, bars, date_from, date_to):
        """The closing count that stands per (bar, day), as on the Desk."""
        counts = self.env["odin.bar.count"].sudo().search(
            [
                ("bar_id", "in", bars.ids),
                ("kind", "=", "closing"),
                ("business_date", ">=", date_from),
                ("business_date", "<=", date_to),
                ("state", "!=", "cancel"),
            ],
            order="id desc",
        )
        standing = {}
        for count in counts:
            standing.setdefault((count.bar_id.id, count.business_date), count)
        return standing

    @api.model
    def _pos_missing(self, bar, days):
        """Days of ``days`` whose POS sales ``bar`` still waits for."""
        if not bar._pos_tracked():
            return set()
        PosDay = self.env["odin.bar.pos.day"].sudo()
        start = bar.pos_start_date or PosDay.search(
            [("bar_id", "=", bar.id)], order="business_date", limit=1
        ).business_date
        posted = set(
            PosDay.search([("bar_id", "=", bar.id), ("business_date", "in", days)]).mapped("business_date")
        )
        return {day for day in days if (not start or day >= start) and day not in posted}

    # ------------------------------------------------------------------
    # 1. Closing: locations x days
    # ------------------------------------------------------------------

    @api.model
    def _closing(self, all_bars, bars, days, today):
        Desk = self.env["odin.bar.desk"]
        reach = today - timedelta(days=DAYS_BACK)
        counts = self._counts(all_bars, days[0], days[-1])
        first = self.env["odin.bar.count"].sudo().search(
            [("bar_id", "in", all_bars.ids), ("kind", "=", "closing"), ("state", "!=", "cancel")],
            order="business_date",
            limit=1,
        ).business_date
        pos_missing = {bar.id: self._pos_missing(bar, days) for bar in all_bars}
        cells = {}
        for bar in all_bars:
            for day in days:
                count = counts.get((bar.id, day))
                differences, unresolved = Desk._desk_count_differences(count) if count else (0, 0)
                cells[(bar.id, day)] = {
                    "state": count.state if count else "none",
                    "count_id": count.id if count else False,
                    "differences": differences,
                    "unresolved": unresolved,
                    "pos_missing": day in pos_missing[bar.id],
                }
        day_rows = []
        for day in days:
            states = [cells[(bar.id, day)]["state"] for bar in all_bars]
            started = bool(first) and day >= first
            if all(state == "approved" for state in states):
                state = "approved"
            elif not started:
                state = "before"
            elif day < reach:
                state = "missed"
            else:
                state = "open"
            day_rows.append(
                {
                    "date": fields.Date.to_string(day),
                    "label": self._day_label(day),
                    "weekday": day.strftime("%a"),
                    "day": day.day,
                    "state": state,
                    "in_reach": day >= reach,
                }
            )
        rows = []
        for bar in bars:
            row_cells = []
            for day, day_row in zip(days, day_rows, strict=True):
                cell = dict(cells[(bar.id, day)], date=day_row["date"])
                if cell["state"] == "none" and day_row["state"] == "missed":
                    cell["state"] = "missed"
                elif day_row["state"] == "before" and cell["state"] == "none":
                    cell["state"] = "before"
                row_cells.append(cell)
            rows.append({"bar": self._bar_info(bar), "cells": row_cells})
        # The day that needs the controller: the oldest open day the Desk still reaches.
        focus = False
        for day, day_row in zip(days, day_rows, strict=True):
            if day_row["state"] == "open":
                focus = self._focus(all_bars, day, cells, day_row)
                break
        missed = [row for row in day_rows if row["state"] == "missed"]
        return {
            "days": day_rows,
            "rows": rows,
            "focus": focus,
            "missed": [{"date": row["date"], "label": row["label"]} for row in missed],
        }

    @api.model
    def _focus(self, bars, day, cells, day_row):
        """What is left to close ``day``, location by location."""
        items = []
        for bar in bars:
            cell = cells[(bar.id, day)]
            if cell["state"] == "none":
                items.append({"bar": bar.code, "text": _("not counted"), "tone": "attention"})
            elif cell["state"] == "draft":
                items.append({"bar": bar.code, "text": _("counting"), "tone": "attention"})
            elif cell["unresolved"]:
                items.append(
                    {"bar": bar.code, "text": _("%s to explain", cell["unresolved"]), "tone": "attention"}
                )
            if cell["pos_missing"]:
                items.append({"bar": bar.code, "text": _("no POS"), "tone": "attention"})
        return {
            "date": day_row["date"],
            "label": day_row["label"],
            "ready": not items,
            "items": items,
        }

    # ------------------------------------------------------------------
    # 2. Locations
    # ------------------------------------------------------------------

    @api.model
    def _variance_lines(self, bars, date_from, date_to):
        return self.env["odin.bar.count.line"].sudo().search(
            [
                ("bar_id", "in", bars.ids),
                ("state", "=", "approved"),
                ("business_date", ">=", date_from),
                ("business_date", "<=", date_to),
                ("diff_value", "!=", 0),
            ]
        )

    @api.model
    def _flows(self, bars, date_from, date_to):
        """Value at cost of what came in and went out of each location from
        ``date_from`` to ``date_to``, by kind:
        {bar_id: {(kind, direction): (value, moves)}}."""
        History = self.env["odin.bar.stock.history"].sudo()
        result = defaultdict(dict)
        for bar, kind, direction, product, quantity, pickings in History._read_group(
            [
                ("bar_id", "in", bars.ids),
                ("business_date", ">=", date_from),
                ("business_date", "<=", date_to),
                ("kind", "in", ("pos", "move", "supplier")),
            ],
            ["bar_id", "kind", "direction", "product_id"],
            ["quantity:sum", "picking_id:count_distinct"],
        ):
            value, moves = result[bar.id].get((kind, direction), (0.0, 0))
            result[bar.id][(kind, direction)] = (
                value + self._cost(product, abs(quantity)),
                moves + pickings,
            )
        return result

    @api.model
    def _locations(self, bars, days, previous, today):
        """Variance over the closed days; sales and moves up to today."""
        lines = self._variance_lines(bars, days[0], days[-1])
        before = self._variance_lines(bars, *previous)
        flows = self._flows(bars, days[0], today)
        values = self._stock_values(bars)
        cards = []
        for bar in bars:
            own = lines.filtered(lambda line, bar=bar: line.bar_id == bar)
            short = -sum(value for value in own.mapped("diff_value") if value < 0)
            over = sum(value for value in own.mapped("diff_value") if value > 0)
            short_before = -sum(
                value for value in before.filtered(lambda line, bar=bar: line.bar_id == bar).mapped("diff_value") if value < 0
            )
            by_reason = defaultdict(float)
            for line in own.filtered(lambda line: line.diff_value < 0):
                by_reason[line.variance_reason_id] -= line.diff_value
            top = max(by_reason.items(), key=lambda item: item[1], default=None)
            flow = flows.get(bar.id, {})
            sold = flow.get(("pos", "out"), (0.0, 0))[0] - flow.get(("pos", "in"), (0.0, 0))[0]
            last = self.env["odin.bar.count"].sudo().search(
                [("bar_id", "=", bar.id), ("kind", "=", "closing"), ("state", "!=", "cancel")],
                order="business_date desc, id desc",
                limit=1,
            )
            differences, unresolved = (
                self.env["odin.bar.desk"]._desk_count_differences(last) if last else (0, 0)
            )
            cards.append(
                {
                    "bar": self._bar_info(bar),
                    "short": round(short),
                    "over": round(over),
                    "short_before": round(short_before),
                    "short_pct": round(100 * short / sold, 1) if sold > 0 else False,
                    "top_reason": {"name": top[0].name or _("No reason"), "value": round(top[1])} if top else False,
                    "sold": round(sold),
                    "received": round(flow.get(("supplier", "in"), (0.0, 0))[0]),
                    "moves_in": flow.get(("move", "in"), (0.0, 0))[1],
                    "moves_in_value": round(flow.get(("move", "in"), (0.0, 0))[0]),
                    "moves_out": flow.get(("move", "out"), (0.0, 0))[1],
                    "moves_out_value": round(flow.get(("move", "out"), (0.0, 0))[0]),
                    "stock_value": round(values.get(bar.id, 0.0)),
                    "last_count": {
                        "id": last.id,
                        "date": fields.Date.to_string(last.business_date),
                        "label": self._day_label(last.business_date),
                        "state": last.state,
                        "unresolved": unresolved,
                        "differences": differences,
                    }
                    if last
                    else False,
                }
            )
        return cards

    # ------------------------------------------------------------------
    # 3. Variance
    # ------------------------------------------------------------------

    @api.model
    def _variance(self, bars, all_bars, days, previous):
        lines = self._variance_lines(bars, days[0], days[-1])
        before = self._variance_lines(bars, *previous)
        short_lines = lines.filtered(lambda line: line.diff_value < 0)
        short = -sum(short_lines.mapped("diff_value"))
        over = sum(lines.filtered(lambda line: line.diff_value > 0).mapped("diff_value"))
        short_before = -sum(value for value in before.mapped("diff_value") if value < 0)

        reasons = defaultdict(float)
        locations = defaultdict(float)
        products = defaultdict(lambda: [0.0, 0.0])
        for line in short_lines:
            reasons[line.variance_reason_id] -= line.diff_value
            locations[line.bar_id] -= line.diff_value
            products[line.product_id][0] -= line.diff_value
            products[line.product_id][1] -= line.diff_qty
        reason_rows = [
            {
                "id": reason.id,
                "name": reason.name or _("No reason"),
                "unexplained": reason.code == "unexplained" or not reason,
                "value": round(value),
            }
            for reason, value in sorted(reasons.items(), key=lambda item: -item[1])
        ]
        location_rows = [
            {"id": bar.id, "code": bar.code, "name": bar.name, "value": round(locations.get(bar, 0.0))}
            for bar in all_bars
            if bar in bars
        ]
        location_rows.sort(key=lambda row: -row["value"])
        product_rows = [
            {
                "id": product.id,
                "name": product.display_name,
                "value": round(value),
                "qty": self._bulk(product, qty),
            }
            for product, (value, qty) in sorted(products.items(), key=lambda item: -item[1][0])[:TOP_PRODUCTS]
        ]
        return {
            "short": round(short),
            "over": round(over),
            "short_before": round(short_before),
            "trend_pct": round(100 * (short - short_before) / short_before) if short_before else False,
            "reasons": reason_rows,
            "locations": location_rows,
            "products": product_rows,
        }

    # ------------------------------------------------------------------
    # 4. Stock
    # ------------------------------------------------------------------

    @api.model
    def _stock_values(self, bars):
        return {
            bar.id: value
            for bar, value in self.env["odin.bar.stock.report"].sudo()._read_group(
                [("bar_id", "in", bars.ids)], ["bar_id"], ["value:sum"]
            )
        }

    @api.model
    def _stock(self, bars, all_bars):
        """Stock value by location, and the club's stock against par: reorder
        levels are kept for the whole club, so below par always reads the
        club total, whichever location is picked."""
        company = self.env.company
        par = defaultdict(float)
        for point in self.env["stock.warehouse.orderpoint"].sudo().search(
            [("company_id", "=", company.id), ("product_min_qty", ">", 0)]
        ):
            par[point.product_id] += point.product_min_qty
        on_hand = defaultdict(float)
        if par:
            for product, quantity in self.env["odin.bar.stock.report"].sudo()._read_group(
                [("bar_id", "in", all_bars.ids), ("product_id", "in", [p.id for p in par])],
                ["product_id"],
                ["quantity:sum"],
            ):
                on_hand[product] += quantity
        below = []
        for product, minimum in par.items():
            total = on_hand.get(product, 0.0)
            if total < minimum:
                below.append(
                    {
                        "id": product.id,
                        "name": product.display_name,
                        "on_hand": self._bulk(product, max(total, 0.0)),
                        "reorder": self._bulk(product, minimum - total),
                        "ratio": total / minimum if minimum else 0.0,
                    }
                )
        below.sort(key=lambda row: (row["ratio"], row["name"]))
        values = self._stock_values(bars)
        return {
            "below_count": len(below),
            "below": [{key: row[key] for key in ("id", "name", "on_hand", "reorder")} for row in below[:TOP_BELOW_PAR]],
            "values": [
                {"id": bar.id, "code": bar.code, "name": bar.name, "value": round(values.get(bar.id, 0.0))}
                for bar in bars
            ],
            "total": round(sum(values.get(bar.id, 0.0) for bar in bars)),
        }

    # ------------------------------------------------------------------
    # 5. Out of the club
    # ------------------------------------------------------------------

    @api.model
    def _out_pickings(self, bars, date_from, date_to):
        """Moves out of the club (Roma, Event, Unpaid bill...) from ``bars``
        on trading days ``date_from`` to ``date_to``."""
        bars = bars or self._bars()
        date_from = fields.Date.to_date(date_from)
        date_to = fields.Date.to_date(date_to)
        start = bars[0]._business_day_bounds(date_from)[0]
        end = bars[0]._business_day_bounds(date_to)[1]
        return self.env["stock.picking"].sudo().search(
            [
                ("company_id", "=", self.env.company.id),
                ("bar_reason_id", "!=", False),
                ("state", "=", "done"),
                ("location_id", "child_of", bars.location_id.ids),
                "|",
                "&",
                ("bar_business_date", ">=", date_from),
                ("bar_business_date", "<=", date_to),
                "&",
                ("bar_business_date", "=", False),
                "&",
                ("date_done", ">=", start),
                ("date_done", "<", end),
            ],
            order="date_done desc, id desc",
        )

    @api.model
    def _out_of_club(self, bars, date_from, date_to):
        pickings = self._out_pickings(bars, date_from, date_to)
        by_reason = {}
        for picking in pickings:
            value = sum(
                self._cost(move.product_id, move.product_uom._compute_quantity(move.quantity, move.product_id.uom_id))
                for move in picking.move_ids
                if move.state == "done"
            )
            row = by_reason.setdefault(
                picking.bar_reason_id,
                {"id": picking.bar_reason_id.id, "name": picking.bar_reason_id.name, "count": 0, "value": 0.0},
            )
            row["count"] += 1
            row["value"] += value
        reasons = sorted(by_reason.values(), key=lambda row: -row["value"])
        for row in reasons:
            row["value"] = round(row["value"])
        Desk = self.env["odin.bar.desk"]
        chase = pickings.filtered(lambda picking: picking.bar_reason_id.require_member and not picking.bar_billed)
        return {
            "reasons": reasons,
            "chase_count": len(chase),
            "chase": [
                {
                    "id": picking.id,
                    "member": picking.bar_member_ref or "",
                    "summary": Desk._desk_summary(
                        [(move.product_id, move.product_uom, move.quantity) for move in picking.move_ids if move.state == "done"]
                    ),
                    "label": self._day_label(
                        picking.bar_business_date or bars[0]._business_date(picking.date_done)
                    ),
                }
                for picking in chase[:TO_CHASE]
            ],
        }
