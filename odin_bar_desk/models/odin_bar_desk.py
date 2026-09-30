"""Server side of the Bar Desk client action.

The Desk is the stock controller's tool: one person logs the day's stock
moves between the club's locations, counts every location the next morning
against the stock expected, explains each difference, and a manager approves
the day.

Desk logins may have no rights on stock models. Everything the Desk does goes
through the public ``desk_*`` methods below, which

1. check that the login may act for the location it names (a shared login
   only for its own locations, whatever the client sends),
2. check the signed staff token the PIN sign-in returned and that the staff
   member may work at that location,
3. then act as superuser, in one transaction, stamping every record with the
   staff member.

Actions that post stock take a client-generated ``uuid``. It is stored on the
``odin.bar.activity`` row under a unique index before anything is posted, so a
retry after a dropped connection returns the first result instead of posting
twice.
"""

import time
from collections import defaultdict
from datetime import timedelta

import psycopg2
import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ConcurrencyError, UserError
from odoo.tools import consteq, format_date
from odoo.tools.misc import hmac as hmac_sign

STAFF_GROUP = "odin_bar_desk.group_bar_desk_staff"
MANAGER_GROUP = "odin_bar_desk.group_bar_desk_manager"
TOKEN_SCOPE = "odin_bar_desk.staff"
TOKEN_HOURS = 16
PIN_MAX_FAILURES = 5
PIN_LOCK_MINUTES = 5
# Closing counts can be taken for today and this many trading days back.
DAYS_BACK = 3
MOST_USED_DAYS = 30
MOST_USED_LIMIT = 12
LIST_LIMIT = 50


class DeskSessionError(AccessError):
    """The staff sign-in is missing, expired or not valid for this company.
    The Desk goes back to the PIN screen when it gets this error."""


def _short_uom(uom):
    return uom.name.split("(")[0].strip() or uom.name


class OdinBarDesk(models.AbstractModel):
    _name = "odin.bar.desk"
    _description = "Bar Desk"

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @api.model
    def _desk_is_manager(self):
        return self.env.user.has_group(MANAGER_GROUP)

    @api.model
    def _desk_check_group(self):
        if not (self.env.user.has_group(STAFF_GROUP) or self._desk_is_manager()):
            raise AccessError(_("You are not allowed to use the Bar Desk."))

    @api.model
    def _desk_login_bars(self):
        """Locations this login may open, in Desk order."""
        user = self.env.user
        if self._desk_is_manager():
            bars = self.env["odin.bar"].sudo().search([("company_id", "in", user.company_ids.ids)])
        else:
            bars = user._odin_desk_bars()
        return bars.sorted(lambda bar: (bar.kind != "store", bar.sequence, bar.name, bar.id))

    @api.model
    def _desk_bar(self, bar_id):
        """The location this login may act for, as superuser in its company."""
        self._desk_check_group()
        bar = self.env["odin.bar"].sudo().browse(int(bar_id or 0)).exists()
        if not bar or not bar.active:
            raise AccessError(_("This location does not exist or is archived."))
        if bar not in self._desk_login_bars():
            raise AccessError(_("This login cannot be used for %(bar)s.", bar=bar.name))
        return bar.with_company(bar.company_id)

    @api.model
    def _desk_employee_allowed(self, bar, employee):
        if employee._odin_bar_allowed(bar):
            return True
        # Managers can always sign in as themselves.
        return employee.user_id == self.env.user and self._desk_is_manager()

    @api.model
    def _desk_employees(self, bar):
        HrEmployee = self.env["hr.employee"].sudo()
        employees = HrEmployee.search(
            [
                "|",
                ("odin_bar_ids", "in", bar.id),
                "&",
                ("odin_bar_all", "=", True),
                ("company_id", "=", bar.company_id.id),
            ]
        )
        if self._desk_is_manager():
            employees |= self.env.user.sudo().employee_ids.filtered(
                lambda employee: employee.company_id == bar.company_id
            )
        return employees.sorted("name")

    @api.model
    def _desk_token(self, bar, employee):
        """A sign-in valid at every location of ``bar``'s company the staff
        member may work at, for this login."""
        expiry = int(time.time()) + TOKEN_HOURS * 3600
        payload = f"{self.env.uid}.{bar.company_id.id}.{employee.id}.{expiry}"
        return f"{payload}.{hmac_sign(self.env(su=True), TOKEN_SCOPE, payload)}"

    @api.model
    def _desk_employee(self, bar, token):
        """The staff member the token was signed for, if it may act at ``bar``."""
        try:
            uid, company_id, employee_id, expiry, signature = str(token or "").split(".")
            expected = hmac_sign(
                self.env(su=True), TOKEN_SCOPE, ".".join((uid, company_id, employee_id, expiry))
            )
            valid = (
                consteq(signature.encode(), expected.encode())
                and int(uid) == self.env.uid
                and int(company_id) == bar.company_id.id
                and int(expiry) > time.time()
            )
        except ValueError:
            valid = False
        if not valid:
            raise DeskSessionError(_("Please sign in again."))
        employee = self.env["hr.employee"].sudo().browse(int(employee_id)).exists()
        if not employee or not employee.active:
            raise DeskSessionError(_("Please sign in again."))
        if not self._desk_employee_allowed(bar, employee):
            raise UserError(_("%(name)s does not work at %(bar)s.", name=employee.name, bar=bar.name))
        return employee

    @api.model
    def _desk_context(self, bar_id, token):
        bar = self._desk_bar(bar_id)
        return bar, self._desk_employee(bar, token)

    @api.model
    def _desk_other_bar(self, bar, other_id):
        """Another location of ``bar``'s company this login and signed-in staff
        member may act for."""
        other = self.env["odin.bar"].sudo().browse(int(other_id or 0)).exists()
        if not other or not other.active or other.company_id != bar.company_id:
            raise UserError(_("Pick a location."))
        return self._desk_bar(other.id)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @api.model
    def _desk_day_label(self, day):
        return format_date(self.env, day, date_format="EEE d MMM")

    @api.model
    def _desk_time_label(self, bar, moment):
        if not moment:
            return ""
        return pytz.utc.localize(moment).astimezone(bar._bar_tz()).strftime("%H:%M")

    @api.model
    def _desk_bar_info(self, bar):
        day = bar._business_date()
        return {
            "id": bar.id,
            "name": bar.name,
            "code": bar.code,
            "kind": bar.kind,
            "business_date": fields.Date.to_string(day),
            "day_label": self._desk_day_label(day),
            # The day the sheet opens on: yesterday, closed this morning.
            "closing_label": self._desk_day_label(day - timedelta(days=1)),
        }

    @api.model
    def _desk_summary(self, items):
        text = ", ".join(f"{product.name}: {qty:g} {_short_uom(uom)}" for product, uom, qty in items)
        return text if len(text) <= 250 else text[:247] + "..."

    @api.model
    def _desk_value(self, bar, items):
        total = 0.0
        for product, uom, qty in items:
            product = product.with_company(bar.company_id)
            total += uom._compute_quantity(qty, product.uom_id, round=False) * product.standard_price
        return bar.company_id.currency_id.round(total)

    # ------------------------------------------------------------------
    # Request ids
    # ------------------------------------------------------------------

    @api.model
    def _desk_replay(self, uuid):
        """The activity already recorded for ``uuid``, if any."""
        if not uuid or not isinstance(uuid, str) or len(uuid) > 64:
            raise UserError(_("This request has no valid id. Reload the Desk and try again."))
        return self.env["odin.bar.activity"].sudo().search([("uuid", "=", uuid)], limit=1)

    @api.model
    def _desk_begin(self, uuid, vals):
        """Record the action before posting anything. A retry with the same
        uuid running at the same time waits on the unique index; it is then
        retried in a fresh transaction, finds this row and replays it."""
        try:
            with self.env.cr.savepoint():
                return self.env["odin.bar.activity"].sudo().create(dict(vals, uuid=uuid))
        except psycopg2.errors.UniqueViolation as exc:
            raise ConcurrencyError(f"Bar Desk request {uuid} is already being processed") from exc

    @api.model
    def _desk_result(self, activity):
        return {
            "activity_id": activity.id,
            "kind": activity.kind,
            "summary": activity.summary or "",
            "references": activity.picking_ids.mapped("name") + activity.scrap_ids.mapped("name"),
            "count_id": activity.count_id.id,
        }

    # ------------------------------------------------------------------
    # Stock helpers
    # ------------------------------------------------------------------

    @api.model
    def _desk_items(self, bar, lines):
        """Client lines ``[{product_id, uom_id, qty}]`` as ``[(product, uom, qty)]``,
        refusing products not offered at this bar and units that are not the
        product's own or one of its packagings."""
        allowed = {product.id: product for product in bar._desk_products()}
        Uom = self.env["uom.uom"].sudo()
        items = []
        for line in lines or []:
            product = allowed.get(int(line.get("product_id") or 0))
            if not product:
                raise UserError(_("This product is not offered at %(bar)s.", bar=bar.name))
            uom = Uom.browse(int(line.get("uom_id") or product.uom_id.id)).exists()
            if uom != product.uom_id and uom not in product.product_tmpl_id._bar_pack_uoms():
                raise UserError(
                    _("That is not a unit of %(product)s.", product=product.name)
                )
            qty = float(line.get("qty") or 0.0)
            if qty < 0:
                raise UserError(_("Quantities cannot be negative."))
            if not uom.is_zero(qty):
                items.append((product, uom, uom.round(qty)))
        if not items:
            raise UserError(_("Add at least one item."))
        return items

    @api.model
    def _desk_picking(self, bar, picking_type, source, destination, items, vals=None, validate=True):
        picking = (
            self.env["stock.picking"]
            .sudo()
            .with_company(bar.company_id)
            .create(
                dict(
                    vals or {},
                    picking_type_id=picking_type.id,
                    location_id=source.id,
                    location_dest_id=destination.id,
                    move_ids=[
                        Command.create(
                            {
                                "product_id": product.id,
                                "product_uom": uom.id,
                                "product_uom_qty": qty,
                                "location_id": source.id,
                                "location_dest_id": destination.id,
                            }
                        )
                        for product, uom, qty in items
                    ],
                )
            )
        )
        if validate:
            self._desk_validate(picking)
        return picking

    @api.model
    def _desk_validate(self, picking, quantities=None):
        """Validate ``picking`` now, for exactly what was handed over: the full
        demand, or ``quantities`` ({move id: quantity in the move's unit}), even
        when the books show less at the source. Without ``quantities`` nothing is
        left behind; with them, a shortfall becomes a backorder."""
        if picking.state == "draft":
            picking.action_confirm()
        for move in picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel")):
            move.quantity = move.product_uom_qty if quantities is None else quantities.get(move.id, 0.0)
            move.picked = True
        picking.with_context(
            skip_backorder=True,
            picking_ids_not_to_backorder=picking.ids if quantities is None else [],
        ).button_validate()
        if picking.state != "done":
            raise UserError(_("%(picking)s could not be validated.", picking=picking.name))


    # ------------------------------------------------------------------
    # Start-up and sign-in
    # ------------------------------------------------------------------

    @api.model
    def desk_boot(self):
        """Locations this login can open, and the one to sign in at."""
        user = self.env.user
        self._desk_check_group()
        bars = self._desk_login_bars()
        own = user.sudo().odin_bar_id
        current = own if own in bars else bars[:1]
        return {
            "user_name": user.name,
            "is_manager": self._desk_is_manager(),
            "bars": [{"id": bar.id, "name": bar.name, "code": bar.code, "kind": bar.kind} for bar in bars],
            "bar_id": current.id,
        }

    @api.model
    def desk_open_bar(self, bar_id):
        """The sign-in screen: who can sign in here."""
        bar = self._desk_bar(bar_id)
        return {
            "bar": self._desk_bar_info(bar),
            "employees": [
                {
                    "id": employee.id,
                    "name": employee.name,
                    "has_pin": bool(employee.pin),
                    "is_me": employee.user_id == self.env.user,
                }
                for employee in self._desk_employees(bar)
            ],
        }

    @api.model
    def desk_login(self, bar_id, employee_id, pin=None):
        """Check a staff PIN and return the token the other calls need.

        A wrong PIN is returned as an error rather than raised, so that the
        failure is recorded and repeated guesses lock the employee out for a
        few minutes."""
        bar = self._desk_bar(bar_id)
        employee = self.env["hr.employee"].sudo().browse(int(employee_id or 0)).exists()
        if not employee or not employee.active or not self._desk_employee_allowed(bar, employee):
            raise AccessError(_("This person cannot sign in at %(bar)s.", bar=bar.name))
        manager_self = employee.user_id == self.env.user and self._desk_is_manager()
        if pin or not manager_self:
            Activity = self.env["odin.bar.activity"].sudo()
            failures = Activity.search_count(
                [
                    ("kind", "=", "pin_fail"),
                    ("employee_id", "=", employee.id),
                    ("date", ">=", fields.Datetime.now() - timedelta(minutes=PIN_LOCK_MINUTES)),
                ]
            )
            if failures >= PIN_MAX_FAILURES:
                return {
                    "error": _(
                        "Too many wrong PINs. Try again in %(minutes)s minutes.",
                        minutes=PIN_LOCK_MINUTES,
                    )
                }
            if not employee.pin:
                return {"error": _("%(name)s has no PIN yet. Ask a manager to set one.", name=employee.name)}
            if not consteq(str(employee.pin).encode(), str(pin or "").encode()):
                Activity.create(
                    {
                        "kind": "pin_fail",
                        "bar_id": bar.id,
                        "employee_id": employee.id,
                        "business_date": bar._business_date(),
                        "summary": _("Wrong PIN"),
                    }
                )
                return {"error": _("Wrong PIN.")}
        return {
            "token": self._desk_token(bar, employee),
            "employee": {"id": employee.id, "name": employee.name},
        }

    # ------------------------------------------------------------------
    # The day sheet
    # ------------------------------------------------------------------

    @api.model
    def _desk_day(self, bar, business_date=False):
        """The trading day to work on: ``business_date``, by default yesterday,
        since the day is closed the next morning."""
        today = bar._business_date()
        day = fields.Date.to_date(business_date) if business_date else today - timedelta(days=1)
        if not today - timedelta(days=DAYS_BACK) <= day <= today:
            raise UserError(
                _("Only the last %(days)s trading days and today can be worked on.", days=DAYS_BACK)
            )
        return day

    @api.model
    def _desk_day_bars(self, bar):
        """The locations on the day sheet: every location of ``bar``'s company
        this login may open, the store first."""
        return self._desk_login_bars().filtered(lambda other: other.company_id == bar.company_id)

    @api.model
    def _desk_closing(self, bar, day):
        """The closing count of ``bar`` for ``day`` that stands, if any."""
        return self.env["odin.bar.count"].sudo().search(
            [
                ("bar_id", "=", bar.id),
                ("kind", "=", "closing"),
                ("business_date", "=", day),
                ("state", "!=", "cancel"),
            ],
            order="id desc",
            limit=1,
        )

    @api.model
    def _desk_count_differences(self, count):
        """(differences, unresolved) of a count: lines that differ from the
        expected quantity, and those of them without a reason."""
        if count.state == "approved":
            lines = count.line_ids.filtered(lambda line: not line.product_uom_id.is_zero(line.diff_qty))
            return len(lines), 0
        if count.state not in ("submitted", "recount"):
            return 0, 0
        differing = [
            line for line, diff in count._variances().items() if not line.product_id.uom_id.is_zero(diff)
        ]
        return len(differing), len([line for line in differing if not line.variance_reason_id])

    @api.model
    def _desk_day_state(self, bars, day):
        """'approved' when every location's closing count for ``day`` is
        approved, 'open' when some counting started, 'none' otherwise."""
        states = [self._desk_closing(bar, day).state for bar in bars]
        if states and all(state == "approved" for state in states):
            return "approved"
        return "open" if any(states) else "none"

    @api.model
    def _desk_location_name(self, location):
        bar = self.env["odin.bar"].sudo().search([("location_id", "=", location.id)], limit=1)
        return bar.name or location.name

    @api.model
    def _desk_move_info(self, bar, picking):
        destination = picking.bar_reason_id.name or self._desk_location_name(picking.location_dest_id)
        if picking.bar_member_ref:
            destination = f"{destination}: {picking.bar_member_ref}"
        return {
            "id": picking.id,
            "name": picking.name,
            "time": self._desk_time_label(bar, picking.date_done),
            "day_label": self._desk_day_label(picking.bar_business_date) if picking.bar_business_date else "",
            "from": self._desk_location_name(picking.location_id),
            "to": destination,
            "summary": self._desk_summary(
                [(move.product_id, move.product_uom, move.quantity) for move in picking.move_ids if move.state == "done"]
            ),
            "who": picking.bar_employee_id.name or picking.create_uid.name,
        }

    @api.model
    def _desk_day_moves(self, bar, day):
        """Moves logged by hand for ``day``, or on ``day`` without a trading day."""
        start, end = bar._business_day_bounds(day)
        pickings = self.env["stock.picking"].sudo().search(
            [
                ("company_id", "=", bar.company_id.id),
                ("bar_manual_move", "=", True),
                ("state", "=", "done"),
                "|",
                ("bar_business_date", "=", day),
                "&",
                ("bar_business_date", "=", False),
                "&",
                ("date_done", ">=", start),
                ("date_done", "<", end),
            ],
            order="date_done desc, id desc",
            limit=LIST_LIMIT,
        )
        return [self._desk_move_info(bar, picking) for picking in pickings]

    @api.model
    def desk_home(self, bar_id, token, business_date=False):
        """The day sheet: POS sales, moves, counts and differences of every
        location for trading day ``business_date`` (default yesterday), and
        whether the day can be approved."""
        bar, employee = self._desk_context(bar_id, token)
        day = self._desk_day(bar, business_date)
        today = bar._business_date()
        bars = self._desk_day_bars(bar)
        locations = []
        for location in bars:
            count = self._desk_closing(location, day)
            differences, unresolved = self._desk_count_differences(count)
            pos_missing = location._pos_missing_day(day) if location._pos_tracked() else False
            locations.append(
                {
                    "id": location.id,
                    "name": location.name,
                    "code": location.code,
                    "kind": location.kind,
                    "count_id": count.id,
                    "state": count.state or "none",
                    "counted": count.counted_count,
                    "total": count.line_count,
                    "differences": differences,
                    "unresolved": unresolved,
                    "pos": "none" if not location._pos_tracked() else ("missing" if pos_missing else "posted"),
                    "pos_missing_label": self._desk_day_label(pos_missing) if pos_missing else "",
                    "can_import_pos": bool(pos_missing and self._desk_is_manager() and location._pos_import_action(pos_missing)),
                }
            )
        not_counted = [loc["name"] for loc in locations if loc["state"] in ("none", "draft")]
        pos_missing = [loc for loc in locations if loc["pos"] == "missing"]
        unresolved = sum(loc["unresolved"] for loc in locations)
        approved = bool(locations) and all(loc["state"] == "approved" for loc in locations)
        blocked = False
        if approved:
            blocked = _("The day is approved.")
        elif pos_missing:
            blocked = _(
                "Import the POS sales of %(bars)s first.",
                bars=", ".join(f"{loc['name']} ({loc['pos_missing_label']})" for loc in pos_missing),
            )
        elif not_counted:
            blocked = _("Count %(bars)s first.", bars=", ".join(not_counted))
        elif unresolved:
            blocked = _("Give a reason for the %(count)s difference(s) left.", count=unresolved)
        elif not self._desk_is_manager():
            blocked = _("A manager approves the day.")
        store = bars.filtered(lambda other: other.kind == "store")[:1]
        days = []
        for back in range(DAYS_BACK, -1, -1):
            other_day = today - timedelta(days=back)
            label = {0: _("Today"), 1: _("Yesterday")}.get(back) or self._desk_day_label(other_day)
            days.append(
                {
                    "date": fields.Date.to_string(other_day),
                    "label": label,
                    "state": self._desk_day_state(bars, other_day),
                }
            )
        return {
            "bar": self._desk_bar_info(bar),
            "employee": {"id": employee.id, "name": employee.name},
            "is_manager": self._desk_is_manager(),
            "currency": bar.company_id.currency_id.symbol or bar.company_id.currency_id.name,
            "day": {
                "date": fields.Date.to_string(day),
                "label": self._desk_day_label(day),
                "is_today": day == today,
            },
            "days": days,
            "locations": locations,
            "moves": self._desk_day_moves(bar, day),
            "counted": len(locations) - len(not_counted),
            "differences": sum(loc["differences"] for loc in locations),
            "unresolved": unresolved,
            "approved": approved,
            "can_approve": not blocked,
            "approve_blocked": blocked or "",
            "store_id": store.id,
            "payment_account": store.supplier_payment_journal_id.name or "",
            "receipts": self.env["stock.picking"].sudo().search_count(self._desk_receipts_domain(store))
            if store
            else 0,
        }

    @api.model
    def desk_pos_import(self, bar_id, token, location_id, business_date):
        """The backend action that imports the missing POS day of a location."""
        bar, _employee = self._desk_context(bar_id, token)
        if not self._desk_is_manager():
            raise AccessError(_("Only a manager imports POS sales."))
        location = self._desk_other_bar(bar, location_id)
        missing = location._pos_missing_day(self._desk_day(bar, business_date))
        action = missing and location._pos_import_action(missing)
        if not action:
            raise UserError(_("The POS sales of %(bar)s are already in.", bar=location.name))
        return action

    # ------------------------------------------------------------------
    # Catalogue
    # ------------------------------------------------------------------

    @api.model
    def desk_catalog(self, bar_id, token):
        """Products, categories, locations and reasons the screens pick from."""
        bar, _employee = self._desk_context(bar_id, token)
        products = bar._desk_products()
        ranks = self._desk_most_used(bar, products)
        infos = products._bar_desk_info()
        for position, info in enumerate(infos):
            info["position"] = position
            info["rank"] = ranks.get(info["id"], 0)
        categories = [
            {"id": category.id, "name": category.name}
            for category in products.categ_id.sorted(lambda c: (c.bar_count_sequence, c.complete_name or ""))
        ]
        company = [("company_id", "=", bar.company_id.id)]
        destinations = self.env["odin.bar.reason"].sudo().search(company + [("operation", "=", "picking")])
        variance_reasons = self.env["odin.bar.variance.reason"].sudo().search(company)
        return {
            "products": infos,
            "categories": categories,
            "locations": [
                {"id": other.id, "name": other.name, "code": other.code, "kind": other.kind}
                for other in self._desk_day_bars(bar)
            ],
            "destinations": [
                {
                    "id": reason.id,
                    "name": reason.name,
                    "icon": reason.icon or "fa-sign-out",
                    "require_member": reason.require_member,
                    "setup_issue": reason.setup_issue or "",
                }
                for reason in destinations
            ],
            "variance_reasons": [
                {"id": reason.id, "name": reason.name, "code": reason.code, "action": reason.action, "icon": reason.icon}
                for reason in variance_reasons
            ],
        }

    @api.model
    def _desk_most_used(self, bar, products):
        """{product id: rank} for the products that moved most in the company lately."""
        groups = (
            self.env["stock.move"]
            .sudo()
            ._read_group(
                [
                    ("state", "=", "done"),
                    ("date", ">=", fields.Datetime.now() - timedelta(days=MOST_USED_DAYS)),
                    ("product_id", "in", products.ids),
                    ("company_id", "=", bar.company_id.id),
                ],
                ["product_id"],
                ["__count"],
                order="__count desc",
                limit=MOST_USED_LIMIT,
            )
        )
        return {product.id: rank for rank, (product, _count) in enumerate(groups, start=1)}

    # ------------------------------------------------------------------
    # Moves
    # ------------------------------------------------------------------

    @api.model
    def _desk_move_type(self, source, dest):
        """Operation type for stock going from location ``source`` to ``dest``:
        the store's issue to that bar, the bar's return to the store, the
        inter-bar type, or the warehouse's internal type."""
        if source.kind == "store" and dest.issue_type_id:
            return dest.issue_type_id
        if dest.kind == "store" and source.return_type_id:
            return source.return_type_id
        PickingType = self.env["stock.picking.type"].sudo()
        company = [("company_id", "=", source.company_id.id)]
        reason = self.env["odin.bar.reason"].sudo().search(company + [("operation", "=", "transfer")], limit=1)
        picking_type = (
            reason.picking_type_id
            or PickingType.search(company + [("sequence_code", "=", "IBT")], limit=1)
            or source.location_id.warehouse_id.int_type_id
            or PickingType.search(company + [("code", "=", "internal")], limit=1)
        )
        if not picking_type:
            raise UserError(_("There is no internal transfer type to move stock with."))
        return picking_type

    @api.model
    def _desk_check_day_open(self, bars, day):
        for bar in bars:
            if self._desk_closing(bar, day).state == "approved":
                raise UserError(
                    _(
                        "%(day)s is already approved at %(bar)s. Log the move on today instead.",
                        day=self._desk_day_label(day),
                        bar=bar.name,
                    )
                )

    @api.model
    def desk_move(
        self,
        bar_id,
        token,
        uuid,
        from_bar_id,
        lines,
        to_bar_id=False,
        reason_id=False,
        business_date=False,
        member_ref=False,
        note=False,
    ):
        """Log stock moved from one location to another location of the club,
        or out of the club (``reason_id``: Roma, an event, an unpaid bill).
        Posted and validated at once. With ``business_date`` the move belongs
        to that trading day (typically yesterday, from the paper sheet): the
        closing counts of that day expect it, whenever it is logged. Without,
        it is placed by the time it is logged."""
        bar, employee = self._desk_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        source = self._desk_other_bar(bar, from_bar_id)
        self._desk_employee(source, token)
        reason = self.env["odin.bar.reason"]
        dest_bar = self.env["odin.bar"]
        member_ref = (member_ref or "").strip()
        if reason_id:
            reason = self.env["odin.bar.reason"].sudo().browse(int(reason_id)).exists()
            if not reason or not reason.active or reason.company_id != bar.company_id or reason.operation != "picking":
                raise UserError(_("Pick where the stock went."))
            if reason.require_member and not member_ref:
                raise UserError(_("Enter the member name or number."))
            picking_type = reason.picking_type_id
            destination = picking_type.default_location_dest_id
            if not picking_type or not destination:
                raise UserError(_("Set the operation type and its destination on %(reason)s first.", reason=reason.name))
        else:
            dest_bar = self._desk_other_bar(bar, to_bar_id)
            self._desk_employee(dest_bar, token)
            if dest_bar == source:
                raise UserError(_("The stock must go to another location."))
            picking_type = self._desk_move_type(source, dest_bar)
            destination = dest_bar.location_id
        day = False
        if business_date:
            day = self._desk_day(bar, business_date)
            self._desk_check_day_open(source | dest_bar, day)
        items = self._desk_items(source, lines)
        activity = self._desk_begin(
            uuid,
            {
                "kind": "move",
                "bar_id": source.id,
                "dest_bar_id": dest_bar.id,
                "reason_id": reason.id,
                "employee_id": employee.id,
                "business_date": day or source._business_date(),
                "member_ref": member_ref or False,
                "note": (note or "").strip() or False,
                "summary": self._desk_summary(items),
                "amount": self._desk_value(source, items),
            },
        )
        self._desk_picking(
            source,
            picking_type,
            source.location_id,
            destination,
            items,
            {
                "bar_employee_id": employee.id,
                "bar_activity_id": activity.id,
                "bar_manual_move": True,
                "bar_business_date": day,
                "bar_reason_id": reason.id,
                "bar_member_ref": member_ref or False,
                "note": activity.note or False,
                "origin": _("Logged on the Desk by %(name)s", name=employee.name),
            },
        )
        return self._desk_result(activity)

    # ------------------------------------------------------------------
    # Counts
    # ------------------------------------------------------------------

    @api.model
    def desk_count_start(self, bar_id, token, business_date=False, restart=False):
        """Open the closing count of this location for ``business_date``
        (default yesterday): the draft already started, or a new one.
        ``restart`` drops the draft and starts over."""
        bar, employee = self._desk_context(bar_id, token)
        day = self._desk_day(bar, business_date)
        Count = self.env["odin.bar.count"].sudo()
        if self._desk_closing(bar, day).state == "approved":
            raise UserError(
                _("The count of %(bar)s for %(day)s is already approved.", bar=bar.name, day=self._desk_day_label(day))
            )
        count = Count.search(
            [("bar_id", "=", bar.id), ("kind", "=", "closing"), ("business_date", "=", day), ("state", "=", "draft")],
            order="id desc",
            limit=1,
        )
        if count and restart:
            count.state = "cancel"
            count = Count.browse()
        if not count:
            count = self._desk_count_create(bar, employee, day)
        return self._desk_count_data(count)

    @api.model
    def _desk_count_create(self, bar, employee, day):
        products = bar._desk_sheet_products()
        vals = {
            "bar_id": bar.id,
            "kind": "closing",
            "business_date": day,
            "employee_id": employee.id,
            "line_ids": [
                Command.create({"product_id": product.id, "sequence": sequence})
                for sequence, product in enumerate(products, start=1)
            ],
        }
        try:
            with self.env.cr.savepoint():
                return self.env["odin.bar.count"].sudo().create(vals)
        except psycopg2.errors.UniqueViolation as exc:
            # Someone else started the same closing count a moment ago.
            raise ConcurrencyError("closing count already started") from exc

    @api.model
    def _desk_count_data(self, count):
        bar = count.bar_id
        cutoff = count.submitted_at or fields.Datetime.now()
        breakdown = bar._count_breakdown(count.line_ids.product_id, count.business_date, cutoff)
        pos_missing = bar._pos_missing_day(count.business_date)
        return {
            "id": count.id,
            "name": count.name,
            "kind": count.kind,
            "state": count.state,
            "bar": {"id": bar.id, "name": bar.name, "code": bar.code, "kind": bar.kind},
            "business_date": fields.Date.to_string(count.business_date),
            "day_label": self._desk_day_label(count.business_date),
            "pos_missing_label": self._desk_day_label(pos_missing) if pos_missing else "",
            "lines": [
                {
                    "product_id": line.product_id.id,
                    "touched": line.touched,
                    "accepted": line.accepted_expected,
                    "unit_qty": line.unit_qty,
                    "bottle_detail": line.bottle_detail or {},
                    "open_tots": line.open_tots,
                    **breakdown.get(line.product_id.id, {}),
                }
                for line in count.line_ids
            ],
        }

    @api.model
    def _desk_count_record(self, bar, count_id):
        count = self.env["odin.bar.count"].sudo().browse(int(count_id or 0)).exists()
        if not count or count.bar_id != bar:
            raise UserError(_("This count does not belong to %(bar)s.", bar=bar.name))
        if count.state != "draft":
            raise UserError(_("This count was already submitted."))
        return count

    @api.model
    def _desk_count_write(self, bar, count, lines):
        """Store counted quantities. Each line is sent whole, so saving the
        same lines twice changes nothing."""
        allowed = {product.id: product for product in bar._desk_products()}
        existing = {line.product_id.id: line for line in count.line_ids}
        to_create = []
        for data in lines or []:
            product = allowed.get(int(data.get("product_id") or 0))
            if not product:
                raise UserError(_("This product is not offered at %(bar)s.", bar=bar.name))
            vals = self._desk_count_line_vals(product, data)
            line = existing.get(product.id)
            if line:
                line.write(vals)
            else:
                to_create.append(
                    dict(vals, count_id=count.id, product_id=product.id, sequence=len(existing) + len(to_create) + 1)
                )
        if to_create:
            self.env["odin.bar.count.line"].sudo().create(to_create)

    @api.model
    def _desk_count_line_vals(self, product, data):
        vals = {
            "touched": bool(data.get("touched", True)),
            "accepted_expected": bool(data.get("accepted")),
            "unit_qty": 0.0,
            "open_tots": 0.0,
            "bottle_detail": False,
        }
        if product.bar_bottle_uom_id:
            sizes = {str(uom.id) for uom in product.product_tmpl_id._bar_pack_uoms()}
            detail = {}
            for key, value in (data.get("bottle_detail") or {}).items():
                if str(key) not in sizes:
                    raise UserError(_("Unknown bottle size for %(product)s.", product=product.name))
                bottles = float(value or 0.0)
                if bottles < 0:
                    raise UserError(_("Counts cannot be negative."))
                if bottles:
                    detail[str(key)] = bottles
            vals["bottle_detail"] = detail or False
            vals["open_tots"] = float(data.get("open_tots") or 0.0)
        else:
            vals["unit_qty"] = float(data.get("unit_qty") or 0.0)
        if vals["unit_qty"] < 0 or vals["open_tots"] < 0:
            raise UserError(_("Counts cannot be negative."))
        return vals

    @api.model
    def desk_count_save(self, bar_id, token, count_id, lines):
        """Autosave of a count in progress."""
        bar, _employee = self._desk_context(bar_id, token)
        count = self._desk_count_record(bar, count_id)
        self._desk_count_write(bar, count, lines)
        return {"counted": count.counted_count, "total": count.line_count}

    @api.model
    def desk_count_submit(self, bar_id, token, uuid, count_id, lines=None):
        """Finish a closing count: every line entered, zeros included. It
        replaces any earlier submission for the same location and day that is
        still waiting. Returns how many lines differ from the expected stock."""
        bar, employee = self._desk_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        count = self._desk_count_record(bar, count_id)
        self._desk_count_write(bar, count, lines)
        untouched = count.line_ids.filtered(lambda line: not line.touched)
        if count.kind == "closing" and untouched:
            raise UserError(
                _(
                    "%(count)s items are not counted yet. Enter 0 for anything that has run out.",
                    count=len(untouched),
                )
            )
        activity = self._desk_begin(
            uuid,
            {
                "kind": "count",
                "bar_id": bar.id,
                "employee_id": employee.id,
                "business_date": count.business_date,
                "count_id": count.id,
                "summary": _(
                    "%(bar)s for %(day)s: %(lines)s items",
                    bar=bar.name,
                    day=self._desk_day_label(count.business_date),
                    lines=len(count.line_ids),
                ),
            },
        )
        self.env["odin.bar.count"].sudo().search(
            [
                ("bar_id", "=", bar.id),
                ("kind", "=", count.kind),
                ("business_date", "=", count.business_date),
                ("state", "in", ("submitted", "recount")),
                ("id", "!=", count.id),
            ]
        ).write({"state": "cancel", "replaced_by_id": count.id})
        count.write({"state": "submitted", "submitted_at": fields.Datetime.now(), "employee_id": employee.id})
        result = self._desk_result(activity)
        result["differences"] = self._desk_count_differences(count)[0]
        return result

    # ------------------------------------------------------------------
    # Differences
    # ------------------------------------------------------------------

    @api.model
    def _desk_open_counts(self, bar, day, location=None):
        counts = self.env["odin.bar.count"].sudo()
        for other in location or self._desk_day_bars(bar):
            count = self._desk_closing(other, day)
            if count.state in ("submitted", "recount"):
                counts |= count
        return counts

    @api.model
    def desk_differences(self, bar_id, token, business_date=False, location_id=False):
        """Lines of the day's submitted counts that differ from the expected
        stock, with their reason if they have one, and the missed moves the
        differences suggest: the same product short at one location by
        exactly what another has too much of."""
        bar, _employee = self._desk_context(bar_id, token)
        day = self._desk_day(bar, business_date)
        location = self._desk_other_bar(bar, location_id) if location_id else None
        counts = self._desk_open_counts(bar, day, location)
        lines = []
        for count in counts.sorted(lambda c: (c.bar_id.kind != "store", c.bar_id.sequence, c.bar_id.id)):
            breakdown = count.bar_id._count_breakdown(
                count.line_ids.product_id, count.business_date, count.submitted_at
            )
            for line, diff in count._variances().items():
                product = line.product_id.with_company(count.company_id)
                if product.uom_id.is_zero(diff):
                    continue
                parts = breakdown.get(product.id, {})
                lines.append(
                    {
                        "line_id": line.id,
                        "count_id": count.id,
                        "bar": {"id": count.bar_id.id, "name": count.bar_id.name, "code": count.bar_id.code},
                        "product_id": product.id,
                        "counted": line.counted_qty,
                        "expected": parts.get("expected", line.counted_qty - diff),
                        "opening": parts.get("opening", 0.0),
                        "moved": parts.get("moved", 0.0),
                        "sold": parts.get("sold", 0.0),
                        "diff": diff,
                        "value": count.company_id.currency_id.round(diff * product.standard_price),
                        "reason_id": line.variance_reason_id.id,
                        "note": line.variance_note or "",
                        "recounted": line.recounted,
                        "accepted": line.accepted_expected,
                        "unit_qty": line.unit_qty,
                        "bottle_detail": line.bottle_detail or {},
                        "open_tots": line.open_tots,
                    }
                )
        suggestions = []
        by_product = defaultdict(list)
        for line in lines:
            if not line["reason_id"]:
                by_product[line["product_id"]].append(line)
        for product_id, product_lines in by_product.items():
            uom = self.env["product.product"].browse(product_id).uom_id
            used = set()
            for short in sorted(product_lines, key=lambda l: l["diff"]):
                if short["diff"] >= 0 or short["line_id"] in used:
                    continue
                for extra in product_lines:
                    if (
                        extra["line_id"] not in used
                        and extra["diff"] > 0
                        and extra["bar"]["id"] != short["bar"]["id"]
                        and uom.compare(extra["diff"], -short["diff"]) == 0
                    ):
                        used |= {short["line_id"], extra["line_id"]}
                        suggestions.append(
                            {
                                "product_id": product_id,
                                "qty": extra["diff"],
                                "from": short["bar"],
                                "to": extra["bar"],
                            }
                        )
                        break
        return {
            "day": {"date": fields.Date.to_string(day), "label": self._desk_day_label(day)},
            "lines": lines,
            "suggestions": suggestions,
        }

    @api.model
    def _desk_open_line(self, bar, token, line_id):
        """A line of a submitted count of ``bar``'s company, and who may change it."""
        line = self.env["odin.bar.count.line"].sudo().browse(int(line_id or 0)).exists()
        if not line or line.count_id.company_id != bar.company_id:
            raise UserError(_("This count line does not exist."))
        if line.count_id.state not in ("submitted", "recount"):
            raise UserError(_("%(count)s is not waiting for approval any more.", count=line.count_id.name))
        location = self._desk_bar(line.count_id.bar_id.id)
        return line, location, self._desk_employee(location, token)

    @api.model
    def desk_explain(self, bar_id, token, line_id, reason_id=False, note=False):
        """Give a difference its reason (or clear it). Recorded only: the
        difference is posted under this reason when the day is approved."""
        bar, _employee = self._desk_context(bar_id, token)
        line, location, employee = self._desk_open_line(bar, token, line_id)
        reason = self.env["odin.bar.variance.reason"]
        if reason_id:
            reason = reason.sudo().browse(int(reason_id)).exists()
            if not reason or not reason.active or reason.company_id != bar.company_id:
                raise UserError(_("Pick a reason."))
        note = (note or "").strip()[:200]
        line.write({"variance_reason_id": reason.id, "variance_note": note or False})
        self.env["odin.bar.activity"].sudo().create(
            {
                "kind": "resolve",
                "bar_id": location.id,
                "employee_id": employee.id,
                "business_date": line.count_id.business_date,
                "count_id": line.count_id.id,
                "variance_reason_id": reason.id,
                "note": note or False,
                "summary": _(
                    "%(product)s: %(reason)s", product=line.product_id.display_name, reason=reason.name or _("no reason")
                ),
            }
        )
        return {"line_id": line.id, "reason_id": reason.id, "note": note}

    @api.model
    def desk_count_fix(self, bar_id, token, line_id, value):
        """Correct the count of one line of a submitted count (a counting
        mistake). The line loses its reason: it is checked again."""
        bar, _employee = self._desk_context(bar_id, token)
        line, location, employee = self._desk_open_line(bar, token, line_id)
        before = line.counted_display
        vals = self._desk_count_line_vals(line.product_id, dict(value or {}, touched=True, accepted=False))
        line.write(dict(vals, recounted=True, variance_reason_id=False, variance_note=False))
        self.env["odin.bar.activity"].sudo().create(
            {
                "kind": "fix",
                "bar_id": location.id,
                "employee_id": employee.id,
                "business_date": line.count_id.business_date,
                "count_id": line.count_id.id,
                "summary": _(
                    "%(product)s: %(before)s → %(after)s",
                    product=line.product_id.display_name,
                    before=before or "0",
                    after=line.counted_display or "0",
                ),
            }
        )
        return {"line_id": line.id, "counted": line.counted_qty}

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------

    @api.model
    def desk_approve_day(self, bar_id, token, uuid, business_date):
        """Approve the closing counts of every location for the day at once:
        each difference is posted as a stock adjustment under its reason."""
        bar, employee = self._desk_context(bar_id, token)
        if not self._desk_is_manager():
            raise AccessError(_("Only a manager approves the day."))
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        day = self._desk_day(bar, business_date)
        bars = self._desk_day_bars(bar)
        counts = self.env["odin.bar.count"].sudo()
        not_counted = []
        for location in bars:
            count = self._desk_closing(location, day)
            if count.state in ("submitted", "recount", "approved"):
                counts |= count
            else:
                not_counted.append(location.name)
        if not_counted:
            raise UserError(_("Count %(bars)s first.", bars=", ".join(not_counted)))
        waiting = counts.filtered(lambda count: count.state in ("submitted", "recount")).sorted("submitted_at")
        if not waiting:
            raise UserError(_("%(day)s is already approved.", day=self._desk_day_label(day)))
        for count in waiting:
            reason = count._approve_blocked_reason()
            if reason:
                raise UserError(reason)
        activity = self._desk_begin(
            uuid,
            {
                "kind": "approve",
                "bar_id": bar.id,
                "employee_id": employee.id,
                "business_date": day,
                "summary": _("%(day)s: %(bars)s", day=self._desk_day_label(day), bars=", ".join(waiting.bar_id.mapped("code"))),
            },
        )
        waiting.sudo().action_approve()
        activity.amount = sum(waiting.mapped("diff_value"))
        result = self._desk_result(activity)
        result["variance"] = activity.amount
        return result

    # ------------------------------------------------------------------
    # Stock levels
    # ------------------------------------------------------------------

    @api.model
    def desk_stock_levels(self, bar_id, token):
        """Stock of every Desk product at each location, the club total and
        the reorder level (par), in each product's unit."""
        bar, _employee = self._desk_context(bar_id, token)
        bars = self._desk_day_bars(bar)
        products = bar._desk_products()
        Quant = self.env["stock.quant"].sudo()
        qty = defaultdict(dict)
        for location in bars:
            for product, quantity in Quant._read_group(
                [("location_id", "child_of", location.location_id.id), ("product_id", "in", products.ids)],
                ["product_id"],
                ["quantity:sum"],
            ):
                qty[product.id][location.id] = product.uom_id.round(quantity)
        par = {
            product.id: min_qty
            for product, min_qty in self.env["stock.warehouse.orderpoint"]
            .sudo()
            ._read_group(
                [("product_id", "in", products.ids), ("company_id", "=", bar.company_id.id)],
                ["product_id"],
                ["product_min_qty:sum"],
            )
        }
        rows = []
        for product in products:
            by_location = qty.get(product.id, {})
            total = sum(by_location.values())
            if not by_location and not par.get(product.id):
                continue
            rows.append(
                {
                    "product_id": product.id,
                    "qty": {str(location_id): value for location_id, value in by_location.items()},
                    "total": product.uom_id.round(total),
                    "par": par.get(product.id, 0.0),
                }
            )
        return {
            "locations": [{"id": other.id, "name": other.name, "code": other.code} for other in bars],
            "rows": rows,
        }

    # ------------------------------------------------------------------
    # Supplier deliveries at the store
    # ------------------------------------------------------------------

    @api.model
    def _desk_store(self, bar_id, token):
        store, employee = self._desk_context(bar_id, token)
        if store.kind != "store":
            raise UserError(_("This is only available on the store's Desk."))
        return store, employee

    @api.model
    def _desk_receipts_domain(self, store):
        return [
            ("picking_type_code", "=", "incoming"),
            ("location_dest_id", "=", store.location_id.id),
            ("state", "in", ("confirmed", "waiting", "assigned")),
        ]

    @api.model
    def desk_receipts(self, bar_id, token):
        """Receipts waiting at the store, e.g. from purchase orders."""
        store, _employee = self._desk_store(bar_id, token)
        pickings = self.env["stock.picking"].sudo().search(
            self._desk_receipts_domain(store), order="scheduled_date", limit=LIST_LIMIT
        )
        return [
            {
                "id": picking.id,
                "name": picking.name,
                "partner": picking.partner_id.display_name or "",
                "origin": picking.origin or "",
                "day_label": self._desk_day_label(store._business_date(picking.scheduled_date)),
                "line_count": len(picking.move_ids),
                "billed": picking.bar_billed,
            }
            for picking in pickings
        ]

    @api.model
    def _desk_receipt_record(self, store, picking_id):
        picking = self.env["stock.picking"].sudo().browse(int(picking_id or 0)).exists()
        if not picking or not picking.filtered_domain(self._desk_receipts_domain(store)):
            raise UserError(_("This receipt is not waiting at %(store)s.", store=store.name))
        return picking

    @api.model
    def _desk_receipt_uom(self, move):
        """Unit a supplier delivery line is shown and entered in: the order's
        (crates), not the single units Odoo books the receipt in."""
        order_line = move.purchase_line_id if "purchase_line_id" in move._fields else False
        return (order_line and order_line.product_uom_id) or move.product_uom

    @api.model
    def desk_receipt(self, bar_id, token, picking_id):
        store, _employee = self._desk_store(bar_id, token)
        picking = self._desk_receipt_record(store, picking_id)
        return {
            "id": picking.id,
            "name": picking.name,
            "partner": picking.partner_id.display_name or "",
            "origin": picking.origin or "",
            "lines": [
                {
                    "move_id": move.id,
                    "product_id": move.product_id.id,
                    "name": move.product_id.display_name,
                    "uom_id": uom.id,
                    "uom_name": _short_uom(uom),
                    "pack": uom != move.product_id.uom_id,
                    "qty": move.product_uom._compute_quantity(move.product_uom_qty, uom),
                }
                for move in picking.move_ids.filtered(lambda move: move.state != "cancel")
                for uom in [self._desk_receipt_uom(move)]
            ],
        }

    @api.model
    def desk_receipt_validate(
        self, bar_id, token, uuid, picking_id, received, paid=None, supplier_ref=None, rest="coming"
    ):
        """Validate an expected supplier delivery for what arrived
        (``received``: {move id: quantity in the unit desk_receipt shows}). What is short
        stays expected (``rest`` "coming") or is cancelled ("not_coming").
        ``paid`` ("now" or "later") bills what arrived at the order's prices,
        paid from the store's account when "now"."""
        store, employee = self._desk_store(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        picking = self._desk_receipt_record(store, picking_id)
        if picking.bar_billed:
            paid = None  # the rest of an invoice already billed
        self._desk_check_payment(store, paid)
        quantities = {int(move_id): float(qty or 0.0) for move_id, qty in (received or {}).items()}
        moves = picking.move_ids.filtered(lambda move: move.state not in ("done", "cancel"))
        if any(qty < 0 for qty in quantities.values()) or set(quantities) - set(moves.ids):
            raise UserError(_("These quantities do not match the receipt."))
        shown = {move.id: self._desk_receipt_uom(move) for move in moves}
        items = [
            (move.product_id, shown[move.id], quantities[move.id])
            for move in moves
            if move.id in quantities and not shown[move.id].is_zero(quantities[move.id])
        ]
        if not items:
            raise UserError(_("Nothing was received."))
        activity = self._desk_begin(
            uuid,
            {
                "kind": "receive",
                "bar_id": store.id,
                "partner_id": picking.partner_id.id,
                "employee_id": employee.id,
                "business_date": store._business_date(),
                "source_picking_id": picking.id,
                "summary": self._desk_summary(items),
                "amount": self._desk_value(store, items),
            },
        )
        picking.write({"bar_employee_id": employee.id, "bar_activity_id": activity.id})
        purchase_lines = {move.id: move.purchase_line_id for move in moves}
        self._desk_validate(
            picking,
            {
                move.id: shown[move.id]._compute_quantity(quantities.get(move.id, 0.0), move.product_uom)
                for move in moves
            },
        )
        short = picking.backorder_ids.filtered(lambda backorder: backorder.state not in ("done", "cancel"))
        if short and rest == "not_coming":
            short.action_cancel()
            order = picking.purchase_id
            if order:
                order.message_post(
                    body=_(
                        "%(supplier)s delivered short on %(receipt)s; the rest is not coming (%(who)s).",
                        supplier=picking.partner_id.display_name,
                        receipt=picking.name,
                        who=employee.name,
                    )
                )
        if paid:
            self._desk_supplier_bill(
                store,
                picking.partner_id,
                [
                    (product, uom, qty, purchase_lines[move.id])
                    for move in moves
                    for product, uom, qty in [(move.product_id, shown[move.id], quantities.get(move.id, 0.0))]
                    if not uom.is_zero(qty)
                ],
                paid,
                supplier_ref,
                activity,
                origin=picking.purchase_id.name or picking.name,
            )
        return self._desk_result(activity)
