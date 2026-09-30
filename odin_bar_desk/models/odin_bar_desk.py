"""Server side of the Bar Desk client action.

Staff logins have no rights on stock models. Everything the Desk does goes
through the public ``desk_*`` methods below, which

1. check that the login may act for the bar it names (staff logins only for
   their own bar, whatever the client sends),
2. check the signed staff token the PIN sign-in returned,
3. then act as superuser, in one transaction, stamping every record with the
   staff member.

Actions that post stock take a client-generated ``uuid``. It is stored on the
``odin.bar.activity`` row under a unique index before anything is posted, so a
retry after a dropped connection returns the first result instead of posting
twice.
"""

import time
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
CLOSING_DAYS_BACK = 2
MOST_USED_DAYS = 30
MOST_USED_LIMIT = 12
TIMELINE_LIMIT = 60
LIST_LIMIT = 50


class DeskSessionError(AccessError):
    """The staff sign-in is missing, expired or not valid for this bar. The
    Desk goes back to the PIN screen when it gets this error."""


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
    def _desk_bar(self, bar_id):
        """The bar this login may act for, as superuser in the bar's company."""
        user = self.env.user
        self._desk_check_group()
        bar = self.env["odin.bar"].sudo().browse(int(bar_id or 0)).exists()
        if not bar or not bar.active:
            raise AccessError(_("This bar does not exist or is archived."))
        if self._desk_is_manager():
            if bar.company_id not in user.company_ids:
                raise AccessError(_("You do not have access to %(bar)s.", bar=bar.name))
        elif bar not in user._odin_desk_bars():
            raise AccessError(
                _(
                    "This login can only be used for %(bars)s.",
                    bars=", ".join(user._odin_desk_bars().mapped("name")) or _("no bar"),
                )
            )
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
        expiry = int(time.time()) + TOKEN_HOURS * 3600
        payload = f"{self.env.uid}.{bar.id}.{employee.id}.{expiry}"
        return f"{payload}.{hmac_sign(self.env(su=True), TOKEN_SCOPE, payload)}"

    @api.model
    def _desk_employee(self, bar, token):
        """The staff member the token was signed for, if it is valid here."""
        try:
            uid, bar_id, employee_id, expiry, signature = str(token or "").split(".")
            expected = hmac_sign(
                self.env(su=True), TOKEN_SCOPE, ".".join((uid, bar_id, employee_id, expiry))
            )
            valid = (
                consteq(signature.encode(), expected.encode())
                and int(uid) == self.env.uid
                and int(bar_id) == bar.id
                and int(expiry) > time.time()
            )
        except ValueError:
            valid = False
        if not valid:
            raise DeskSessionError(_("Please sign in again."))
        employee = self.env["hr.employee"].sudo().browse(int(employee_id)).exists()
        if not employee or not employee.active or not self._desk_employee_allowed(bar, employee):
            raise DeskSessionError(_("Please sign in again."))
        return employee

    @api.model
    def _desk_context(self, bar_id, token):
        bar = self._desk_bar(bar_id)
        return bar, self._desk_employee(bar, token)

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

    @api.model
    def _desk_scrap(self, bar, items, reason, vals):
        Scrap = self.env["stock.scrap"].sudo().with_company(bar.company_id)
        scraps = Scrap.browse()
        for product, uom, qty in items:
            scrap = Scrap.create(
                dict(
                    vals,
                    product_id=product.id,
                    product_uom_id=uom.id,
                    scrap_qty=qty,
                    location_id=bar.location_id.id,
                    scrap_reason_tag_ids=[Command.set(reason.scrap_tag_ids.ids)],
                    origin=reason.name,
                )
            )
            scrap.do_scrap()
            scraps |= scrap
        return scraps

    # ------------------------------------------------------------------
    # Start-up and sign-in
    # ------------------------------------------------------------------

    @api.model
    def desk_boot(self):
        """Bars this login can open."""
        user = self.env.user
        self._desk_check_group()
        manager = self._desk_is_manager()
        own = user.sudo().odin_bar_id
        if manager:
            bars = self.env["odin.bar"].sudo().search([("company_id", "in", user.company_ids.ids)])
        else:
            bars = user._odin_desk_bars().sorted(lambda bar: (bar.sequence, bar.name, bar.id))
        current = own if own in bars else bars[:1]
        return {
            "user_name": user.name,
            "is_manager": manager,
            "bars": [
                {"id": bar.id, "name": bar.name, "code": bar.code, "kind": bar.kind}
                for bar in bars
            ],
            "bar_id": current.id,
        }

    @api.model
    def desk_open_bar(self, bar_id):
        """The sign-in screen of a bar: who can sign in there."""
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
    # Home
    # ------------------------------------------------------------------

    @api.model
    def desk_home(self, bar_id, token):
        bar, employee = self._desk_context(bar_id, token)
        day = bar._business_date()
        Picking = self.env["stock.picking"].sudo()
        Request = self.env["odin.bar.request"].sudo()
        result = {
            "bar": self._desk_bar_info(bar),
            "employee": {"id": employee.id, "name": employee.name},
            "count": self._desk_count_status(bar, day),
            "timeline": self._desk_timeline(bar, day),
            "requests_in": Request.search_count([("source_bar_id", "=", bar.id), ("state", "=", "open")]),
            "asks_waiting": Request.search_count([("bar_id", "=", bar.id), ("state", "=", "open")]),
            "asks_missing": len(
                Request.search(
                    [
                        ("bar_id", "=", bar.id),
                        ("state", "in", ("partial", "none")),
                        ("passed_to_ids", "=", False),
                        ("date", ">=", fields.Datetime.now() - timedelta(hours=16)),
                    ]
                )
            ),
            "can_order": bar.kind == "store"
            and bool(employee.sudo().odin_bar_can_order or self._desk_is_manager()),
        }
        if bar.kind == "bar":
            result["unchecked"] = Picking.search_count(
                bar._desk_delivery_domain() + [("bar_ack_state", "=", "not_checked")]
            )
        else:
            result["disputes"] = Picking.search_count(bar._desk_dispute_domain())
            result["receipts"] = Picking.search_count(self._desk_receipts_domain(bar))
            result["payment_account"] = bar.supplier_payment_journal_id.name or ""
        return result

    @api.model
    def _desk_count_status(self, bar, day):
        Count = self.env["odin.bar.count"].sudo()
        latest = Count.search(
            [
                ("bar_id", "=", bar.id),
                ("kind", "=", "closing"),
                ("business_date", "=", day),
                ("state", "!=", "cancel"),
            ],
            order="id desc",
            limit=1,
        )
        recounts = Count.search(
            [("bar_id", "=", bar.id), ("state", "=", "recount")], order="business_date desc"
        )
        days = []
        for back in range(CLOSING_DAYS_BACK + 1):
            other_day = day - timedelta(days=back)
            other = latest if back == 0 else Count.search(
                [
                    ("bar_id", "=", bar.id),
                    ("kind", "=", "closing"),
                    ("business_date", "=", other_day),
                    ("state", "!=", "cancel"),
                ],
                order="id desc",
                limit=1,
            )
            days.append(
                {
                    "business_date": fields.Date.to_string(other_day),
                    "day_label": self._desk_day_label(other_day),
                    "state": other.state or "none",
                }
            )
        return {
            "business_date": fields.Date.to_string(day),
            "day_label": self._desk_day_label(day),
            "state": latest.state or "none",
            "counted": latest.counted_count,
            "total": latest.line_count,
            "days": days,
            "recounts": [
                {
                    "business_date": fields.Date.to_string(count.business_date),
                    "day_label": self._desk_day_label(count.business_date),
                    "kind": count.kind,
                    "note": count.note or "",
                }
                for count in recounts
            ],
        }

    @api.model
    def _desk_timeline(self, bar, day):
        """What happened at this bar during trading day ``day``, newest first."""
        start, end = bar._business_day_bounds(day)
        activities = (
            self.env["odin.bar.activity"]
            .sudo()
            .search(
                [
                    ("kind", "!=", "pin_fail"),
                    ("date", ">=", start),
                    ("date", "<", end),
                    "|",
                    ("bar_id", "=", bar.id),
                    ("dest_bar_id", "=", bar.id),
                ],
                limit=TIMELINE_LIMIT,
            )
        )
        items = [self._desk_timeline_item(bar, activity) for activity in activities]
        # Deliveries booked in the backend rather than from a Desk.
        for picking in (
            self.env["stock.picking"]
            .sudo()
            .search(
                [
                    ("location_dest_id", "=", bar.location_id.id),
                    ("state", "=", "done"),
                    ("bar_ack_state", "!=", False),
                    ("bar_activity_id", "=", False),
                    ("date_done", ">=", start),
                    ("date_done", "<", end),
                ],
                limit=TIMELINE_LIMIT,
            )
        ):
            items.append(
                {
                    "id": f"picking-{picking.id}",
                    "date": fields.Datetime.to_string(picking.date_done),
                    "time": self._desk_time_label(bar, picking.date_done),
                    "icon": "fa-truck",
                    "title": _("Stock in %(name)s", name=picking.name),
                    "detail": picking.location_id.display_name,
                    "who": picking.create_uid.name,
                }
            )
        items.sort(key=lambda item: item["date"], reverse=True)
        return items[:TIMELINE_LIMIT]

    @api.model
    def _desk_timeline_item(self, bar, activity):
        incoming = activity.dest_bar_id == bar and activity.bar_id != bar
        delivery = activity.source_picking_id.name or ""
        titles = {
            "stock_out": activity.reason_id.name or _("Stock out"),
            "count": _("Count submitted"),
            "ack": _("Checked %(delivery)s: all correct", delivery=delivery),
            "dispute": _("Disputed %(delivery)s", delivery=delivery),
            "send": _("Sent to %(bar)s", bar=activity.dest_bar_id.name),
            "receive": _("Supplier delivery from %(supplier)s", supplier=activity.partner_id.name or "?"),
            "ask": _("Asked %(bar)s for stock", bar=activity.dest_bar_id.name),
            "ask_none": _("Could not send to %(bar)s", bar=activity.dest_bar_id.name),
            "order": _("Ordered from %(supplier)s", supplier=activity.partner_id.name or "?"),
            "dispute_accept": _("Dispute on %(delivery)s accepted", delivery=delivery),
            "dispute_reject": _("Dispute on %(delivery)s rejected", delivery=delivery),
        }
        icons = {
            "stock_out": activity.reason_id.icon or "fa-sign-out",
            "count": "fa-list-ol",
            "ack": "fa-check",
            "dispute": "fa-exclamation-triangle",
            "send": "fa-truck",
            "receive": "fa-download",
            "dispute_accept": "fa-check-circle",
            "dispute_reject": "fa-times-circle",
            "ask": "fa-hand-paper-o",
            "ask_none": "fa-ban",
            "order": "fa-shopping-cart",
        }
        title = titles.get(activity.kind, activity.display_name)
        if incoming and activity.kind == "send":
            title = _("Stock in from %(bar)s", bar=activity.bar_id.name)
        elif incoming and activity.kind == "ask":
            title = _("%(bar)s asked for stock", bar=activity.bar_id.name)
        elif incoming and activity.kind == "ask_none":
            title = _("%(bar)s did not have it", bar=activity.bar_id.name)
        elif incoming and activity.kind == "stock_out":
            title = _("Transfer from %(bar)s", bar=activity.bar_id.name)
        elif incoming and activity.kind == "dispute":
            title = _("%(bar)s disputed %(delivery)s", bar=activity.bar_id.name, delivery=delivery)
        elif activity.kind == "count" and activity.count_id:
            title = _("%(count)s submitted", count=dict(
                activity.count_id._fields["kind"]._description_selection(self.env)
            )[activity.count_id.kind])
        detail = activity.summary or ""
        if activity.member_ref:
            detail = _("%(member)s · %(detail)s", member=activity.member_ref, detail=detail)
        return {
            "id": f"activity-{activity.id}",
            "date": fields.Datetime.to_string(activity.date),
            "time": self._desk_time_label(bar, activity.date),
            "icon": icons.get(activity.kind, "fa-circle"),
            "title": title,
            "detail": detail,
            "who": activity.employee_id.name or activity.user_id.name,
        }

    # ------------------------------------------------------------------
    # Catalogue
    # ------------------------------------------------------------------

    @api.model
    def desk_catalog(self, bar_id, token):
        """Products, categories, reasons and bars the screens pick from."""
        bar, _employee = self._desk_context(bar_id, token)
        products = bar._desk_products()
        sheet_ids = set(bar._desk_sheet_products().ids)
        ranks = self._desk_most_used(bar, products)
        infos = products._bar_desk_info()
        for position, info in enumerate(infos):
            info["position"] = position
            info["on_sheet"] = info["id"] in sheet_ids
            info["rank"] = ranks.get(info["id"], 0)
        categories = []
        for category in products.categ_id.sorted(
            lambda c: (c.bar_count_sequence, c.complete_name or "")
        ):
            categories.append({"id": category.id, "name": category.name})
        other_bars = (
            self.env["odin.bar"]
            .sudo()
            .search([("company_id", "=", bar.company_id.id), ("kind", "=", "bar"), ("id", "!=", bar.id)])
        )
        result = {
            "products": infos,
            "categories": categories,
            "bars": [{"id": other.id, "name": other.name} for other in other_bars],
            "reasons": [],
        }
        if bar.kind == "bar":
            reasons = self.env["odin.bar.reason"].sudo().search([("company_id", "=", bar.company_id.id)])
            result["reasons"] = [
                {
                    "id": reason.id,
                    "name": reason.name,
                    "code": reason.code,
                    "operation": reason.operation,
                    "icon": reason.icon or "fa-sign-out",
                    "require_member": reason.require_member,
                    "default_unit": reason.default_unit,
                    "setup_issue": reason.setup_issue or "",
                }
                for reason in reasons
            ]
        return result

    @api.model
    def _desk_most_used(self, bar, products):
        """{product id: rank} for the products that moved most at this bar lately."""
        groups = (
            self.env["stock.move"]
            .sudo()
            ._read_group(
                [
                    ("state", "=", "done"),
                    ("date", ">=", fields.Datetime.now() - timedelta(days=MOST_USED_DAYS)),
                    ("product_id", "in", products.ids),
                    "|",
                    ("location_id", "=", bar.location_id.id),
                    ("location_dest_id", "=", bar.location_id.id),
                ],
                ["product_id"],
                ["__count"],
                order="__count desc",
                limit=MOST_USED_LIMIT,
            )
        )
        return {product.id: rank for rank, (product, _count) in enumerate(groups, start=1)}

    # ------------------------------------------------------------------
    # Stock out
    # ------------------------------------------------------------------

    @api.model
    def desk_stock_out(
        self, bar_id, token, uuid, reason_id, lines, dest_bar_id=False, member_ref=False, note=False
    ):
        """Record stock leaving the bar other than through a POS sale. Posted
        and validated at once."""
        bar, employee = self._desk_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        if bar.kind != "bar":
            raise UserError(_("Stock outs are recorded at a bar."))
        reason = self.env["odin.bar.reason"].sudo().browse(int(reason_id or 0)).exists()
        if not reason or not reason.active or reason.company_id != bar.company_id:
            raise UserError(_("Pick a reason."))
        member_ref = (member_ref or "").strip()
        if reason.require_member and not member_ref:
            raise UserError(_("Enter the member name or number."))
        items = self._desk_items(bar, lines)

        dest_bar = self.env["odin.bar"]
        picking_type = destination = False
        if reason.operation == "transfer":
            dest_bar = self.env["odin.bar"].sudo().browse(int(dest_bar_id or 0)).exists()
            if (
                not dest_bar
                or dest_bar == bar
                or dest_bar.kind != "bar"
                or not dest_bar.active
                or dest_bar.company_id != bar.company_id
            ):
                raise UserError(_("Pick the bar the stock goes to."))
            picking_type, destination = reason.picking_type_id, dest_bar.location_id
        elif reason.operation == "return":
            picking_type = bar.return_type_id
            destination = bar.store_id.location_id or picking_type.default_location_dest_id
            if not picking_type or not destination:
                raise UserError(_("Set the return type and the store on %(bar)s first.", bar=bar.name))
        elif reason.operation == "picking":
            picking_type = reason.picking_type_id
            destination = picking_type.default_location_dest_id
        if reason.operation == "scrap" and not reason.scrap_tag_ids:
            raise UserError(_("Set a scrap reason on %(reason)s first.", reason=reason.name))
        if reason.operation in ("picking", "transfer") and not (picking_type and destination):
            raise UserError(
                _("Set the operation type and its destination on %(reason)s first.", reason=reason.name)
            )

        activity = self._desk_begin(
            uuid,
            {
                "kind": "stock_out",
                "bar_id": bar.id,
                "employee_id": employee.id,
                "business_date": bar._business_date(),
                "reason_id": reason.id,
                "dest_bar_id": dest_bar.id,
                "member_ref": member_ref or False,
                "note": (note or "").strip() or False,
                "summary": self._desk_summary(items),
                "amount": self._desk_value(bar, items),
            },
        )
        vals = {
            "bar_employee_id": employee.id,
            "bar_activity_id": activity.id,
            "bar_reason_id": reason.id,
        }
        if reason.operation == "scrap":
            self._desk_scrap(bar, items, reason, vals)
        else:
            origin = reason.name if not member_ref else f"{reason.name}: {member_ref}"
            vals.update(origin=origin, bar_member_ref=member_ref or False, note=activity.note or False)
            self._desk_picking(bar, picking_type, bar.location_id, destination, items, vals)
        return self._desk_result(activity)

    # ------------------------------------------------------------------
    # Counts
    # ------------------------------------------------------------------

    @api.model
    def desk_count_start(self, bar_id, token, kind="closing", business_date=False, restart=False):
        """Open the count to work on: the draft already started (a closing
        count is shared by everyone at the bar, a spot count belongs to whoever
        started it) or a new one. ``restart`` drops the draft and starts over."""
        bar, employee = self._desk_context(bar_id, token)
        if kind not in ("closing", "spot"):
            raise UserError(_("Unknown kind of count."))
        today = bar._business_date()
        day = fields.Date.to_date(business_date) if business_date and kind == "closing" else today
        if not today - timedelta(days=CLOSING_DAYS_BACK) <= day <= today:
            raise UserError(_("Closing counts can only be taken for the last three trading days."))
        Count = self.env["odin.bar.count"].sudo()
        if kind == "closing" and Count.search_count(
            [
                ("bar_id", "=", bar.id),
                ("kind", "=", "closing"),
                ("business_date", "=", day),
                ("state", "=", "approved"),
            ],
            limit=1,
        ):
            raise UserError(
                _("The closing count for %(day)s is already approved.", day=self._desk_day_label(day))
            )
        domain = [
            ("bar_id", "=", bar.id),
            ("kind", "=", kind),
            ("business_date", "=", day),
            ("state", "=", "draft"),
        ]
        if kind == "spot":
            domain.append(("employee_id", "=", employee.id))
        count = Count.search(domain, order="id desc", limit=1)
        if count and restart:
            count.state = "cancel"
            count = Count.browse()
        if not count:
            count = self._desk_count_create(bar, employee, kind, day)
        return self._desk_count_data(count)

    @api.model
    def _desk_count_create(self, bar, employee, kind, day):
        products = bar._desk_sheet_products() if kind == "closing" else self.env["product.product"]
        vals = {
            "bar_id": bar.id,
            "kind": kind,
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
            # Someone else at the bar started the same closing count a moment ago.
            raise ConcurrencyError("closing count already started") from exc

    @api.model
    def _desk_count_data(self, count):
        return {
            "id": count.id,
            "name": count.name,
            "kind": count.kind,
            "state": count.state,
            "business_date": fields.Date.to_string(count.business_date),
            "day_label": self._desk_day_label(count.business_date),
            "lines": [
                {
                    "product_id": line.product_id.id,
                    "touched": line.touched,
                    "unit_qty": line.unit_qty,
                    "bottle_detail": line.bottle_detail or {},
                    "open_tots": line.open_tots,
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
        vals = {"touched": bool(data.get("touched", True)), "unit_qty": 0.0, "open_tots": 0.0, "bottle_detail": False}
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
        """Hand the count to the manager. A closing count must have every line
        entered, zeros included; it replaces any earlier submission for the
        same bar and day that is still waiting."""
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
        if count.kind == "spot":
            untouched.unlink()
            if not count.line_ids:
                raise UserError(_("Count at least one item."))
        activity = self._desk_begin(
            uuid,
            {
                "kind": "count",
                "bar_id": bar.id,
                "employee_id": employee.id,
                "business_date": count.business_date,
                "count_id": count.id,
                "summary": _(
                    "%(kind)s for %(day)s: %(lines)s items",
                    kind=dict(count._fields["kind"]._description_selection(self.env))[count.kind],
                    day=self._desk_day_label(count.business_date),
                    lines=len(count.line_ids),
                ),
            },
        )
        if count.kind == "closing":
            self.env["odin.bar.count"].sudo().search(
                [
                    ("bar_id", "=", bar.id),
                    ("kind", "=", "closing"),
                    ("business_date", "=", count.business_date),
                    ("state", "in", ("submitted", "recount")),
                    ("id", "!=", count.id),
                ]
            ).write({"state": "cancel", "replaced_by_id": count.id})
        count.write(
            {"state": "submitted", "submitted_at": fields.Datetime.now(), "employee_id": employee.id}
        )
        return self._desk_result(activity)

    # ------------------------------------------------------------------
    # Deliveries into a bar
    # ------------------------------------------------------------------

    @api.model
    def _desk_source_name(self, picking):
        source_bar = self.env["odin.bar"].sudo().search(
            [("location_id", "=", picking.location_id.id)], limit=1
        )
        return source_bar.name or picking.location_id.display_name

    @api.model
    def _desk_delivery_head(self, bar, picking):
        return {
            "id": picking.id,
            "name": picking.name,
            "source": self._desk_source_name(picking),
            "time": self._desk_time_label(bar, picking.date_done),
            "day_label": self._desk_day_label(bar._business_date(picking.date_done)),
            "state": picking.bar_ack_state,
            "line_count": len(picking.move_ids.filtered(lambda move: move.state == "done")),
            "sent_by": picking.bar_employee_id.name or picking.create_uid.name,
            "checked_by": picking.bar_ack_employee_id.name or "",
        }

    @api.model
    def _desk_delivery_record(self, bar, picking_id):
        picking = self.env["stock.picking"].sudo().browse(int(picking_id or 0)).exists()
        if (
            not picking
            or picking.location_dest_id != bar.location_id
            or picking.state != "done"
            or not picking.bar_ack_state
        ):
            raise UserError(_("This stock is not for %(bar)s.", bar=bar.name))
        return picking

    @api.model
    def desk_deliveries(self, bar_id, token):
        bar, _employee = self._desk_context(bar_id, token)
        if bar.kind != "bar":
            return []
        pickings = self.env["stock.picking"].sudo().search(
            bar._desk_delivery_domain(), order="date_done desc", limit=LIST_LIMIT
        )
        return [self._desk_delivery_head(bar, picking) for picking in pickings]

    @api.model
    def desk_delivery(self, bar_id, token, picking_id):
        bar, _employee = self._desk_context(bar_id, token)
        picking = self._desk_delivery_record(bar, picking_id)
        result = self._desk_delivery_head(bar, picking)
        result["lines"] = [
            {
                "move_id": move.id,
                "product_id": move.product_id.id,
                "name": move.product_id.display_name,
                "uom_id": move.product_uom.id,
                "uom_name": _short_uom(move.product_uom),
                "pack": move.product_uom != move.product_id.uom_id,
                "qty": move.quantity,
            }
            for move in picking.move_ids.filtered(lambda move: move.state == "done")
        ]
        result["disputes"] = [
            {"name": dispute.name, "state": dispute.bar_dispute_state} for dispute in picking.bar_dispute_ids
        ]
        return result

    @api.model
    def desk_delivery_check(self, bar_id, token, uuid, picking_id, received=None):
        """Confirm a delivery, or dispute it with what actually arrived.

        ``received`` maps move ids to the quantity counted, in the move's unit;
        moves left out arrived as sent. A shortage becomes a draft return from
        the bar to where the delivery came from, a surplus a draft transfer the
        other way. The sender validates the draft (the goods never left) or
        cancels it (the variance stays with the bar)."""
        bar, employee = self._desk_context(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        picking = self._desk_delivery_record(bar, picking_id)
        if picking.bar_ack_state != "not_checked":
            raise UserError(
                _(
                    "%(delivery)s was already checked by %(who)s.",
                    delivery=picking.name,
                    who=picking.bar_ack_employee_id.name or _("someone else"),
                )
            )
        received = {int(move_id): float(qty or 0.0) for move_id, qty in (received or {}).items()}
        short, extra = [], []
        for move in picking.move_ids.filtered(lambda move: move.state == "done"):
            if move.id not in received:
                continue
            if received[move.id] < 0:
                raise UserError(_("Quantities cannot be negative."))
            diff = move.product_uom.round(received[move.id] - move.quantity)
            if move.product_uom.compare(diff, 0) < 0:
                short.append((move.product_id, move.product_uom, -diff))
            elif move.product_uom.compare(diff, 0) > 0:
                extra.append((move.product_id, move.product_uom, diff))
        source_bar = self.env["odin.bar"].sudo().search([("location_id", "=", picking.location_id.id)], limit=1)
        from_store = not source_bar or source_bar.kind == "store"
        back_type = bar.return_type_id if from_store else picking.picking_type_id
        if short and not back_type:
            raise UserError(_("Set the return type on %(bar)s first.", bar=bar.name))

        summary = []
        if short:
            summary.append(_("Short: %(items)s", items=self._desk_summary(short)))
        if extra:
            summary.append(_("Extra: %(items)s", items=self._desk_summary(extra)))
        activity = self._desk_begin(
            uuid,
            {
                "kind": "dispute" if summary else "ack",
                "bar_id": bar.id,
                "dest_bar_id": source_bar.id,
                "employee_id": employee.id,
                "business_date": bar._business_date(),
                "source_picking_id": picking.id,
                "summary": "; ".join(summary),
            },
        )
        vals = {
            "bar_employee_id": employee.id,
            "bar_activity_id": activity.id,
            "bar_dispute_origin_id": picking.id,
            "origin": picking.name,
        }
        if short:
            self._desk_picking(
                bar, back_type, bar.location_id, picking.location_id, short, vals, validate=False
            )
        if extra:
            self._desk_picking(
                bar, picking.picking_type_id, picking.location_id, bar.location_id, extra, vals, validate=False
            )
        picking.write(
            {
                "bar_ack_state": "disputed" if summary else "confirmed",
                "bar_ack_employee_id": employee.id,
                "bar_ack_date": fields.Datetime.now(),
            }
        )
        return self._desk_result(activity)

    # ------------------------------------------------------------------
    # Store mode
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
    def desk_store_send(self, bar_id, token, uuid, dest_bar_id, lines):
        """Issue stock from the store to a bar, validated at once. It shows at
        the bar as a delivery to check."""
        store, employee = self._desk_store(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        dest = self.env["odin.bar"].sudo().browse(int(dest_bar_id or 0)).exists()
        if not dest or dest.kind != "bar" or not dest.active or dest.company_id != store.company_id:
            raise UserError(_("Pick the bar to send to."))
        if not dest.issue_type_id:
            raise UserError(_("Set the issue type on %(bar)s first.", bar=dest.name))
        items = self._desk_items(store, lines)
        activity = self._desk_begin(
            uuid,
            {
                "kind": "send",
                "bar_id": store.id,
                "dest_bar_id": dest.id,
                "employee_id": employee.id,
                "business_date": store._business_date(),
                "summary": self._desk_summary(items),
                "amount": self._desk_value(store, items),
            },
        )
        self._desk_picking(
            store,
            dest.issue_type_id,
            store.location_id,
            dest.location_id,
            items,
            {"bar_employee_id": employee.id, "bar_activity_id": activity.id},
        )
        return self._desk_result(activity)

    @api.model
    def desk_suppliers(self, bar_id, token, query=""):
        store, _employee = self._desk_store(bar_id, token)
        Partner = self.env["res.partner"].sudo()
        domain = [("company_id", "in", [store.company_id.id, False])]
        if query:
            domain.append(("name", "ilike", query))
        partners = Partner.browse()
        if "supplier_rank" in Partner._fields:
            partners = Partner.search(
                domain + [("supplier_rank", ">", 0)], limit=20, order="supplier_rank desc, name"
            )
        if not partners:
            partners = Partner.search(domain + [("is_company", "=", True)], limit=20, order="name")
        return [{"id": partner.id, "name": partner.display_name} for partner in partners]

    @api.model
    def desk_store_receive(self, bar_id, token, uuid, partner_id, lines, paid=None, supplier_ref=None):
        """Book a supplier delivery that came without an order straight into
        the store, validated at once. ``paid`` ("now" or "later") also bills
        it, paid from the store's account when "now"."""
        store, employee = self._desk_store(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        self._desk_check_payment(store, paid)
        picking_type = store.receipt_type_id
        if not picking_type:
            raise UserError(_("Set the receipt type on %(store)s first.", store=store.name))
        partner = self.env["res.partner"].sudo().browse(int(partner_id or 0)).exists()
        if not partner or partner.company_id not in (store.company_id, self.env["res.company"]):
            raise UserError(_("Pick the supplier."))
        items = self._desk_items(store, lines)
        source = picking_type.default_location_src_id or partner.property_stock_supplier
        activity = self._desk_begin(
            uuid,
            {
                "kind": "receive",
                "bar_id": store.id,
                "partner_id": partner.id,
                "employee_id": employee.id,
                "business_date": store._business_date(),
                "summary": self._desk_summary(items),
                "amount": self._desk_value(store, items),
            },
        )
        picking = self._desk_picking(
            store,
            picking_type,
            source,
            store.location_id,
            items,
            {"partner_id": partner.id, "bar_employee_id": employee.id, "bar_activity_id": activity.id},
        )
        if paid:
            self._desk_supplier_bill(
                store,
                partner,
                [(product, uom, qty, self.env["purchase.order.line"]) for product, uom, qty in items],
                paid,
                supplier_ref,
                activity,
                origin=picking.name,
            )
        return self._desk_result(activity)

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
        self._desk_check_payment(store, paid)
        picking = self._desk_receipt_record(store, picking_id)
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

    @api.model
    def desk_disputes(self, bar_id, token):
        """Open disputes raised by bars against the store's deliveries."""
        store, _employee = self._desk_store(bar_id, token)
        pickings = self.env["stock.picking"].sudo().search(
            store._desk_dispute_domain(), order="id desc", limit=LIST_LIMIT
        )
        Bar = self.env["odin.bar"].sudo()
        result = []
        for picking in pickings:
            other = picking.location_id if picking.location_dest_id == store.location_id else picking.location_dest_id
            result.append(
                {
                    "id": picking.id,
                    "name": picking.name,
                    "delivery": picking.bar_dispute_origin_id.name,
                    "bar": Bar.search([("location_id", "=", other.id)], limit=1).name or other.display_name,
                    "short": picking.location_dest_id == store.location_id,
                    "raised_by": picking.bar_employee_id.name or "",
                    "day_label": self._desk_day_label(store._business_date(picking.create_date)),
                    "lines": [
                        {
                            "name": move.product_id.display_name,
                            "qty": move.product_uom_qty,
                            "uom_name": _short_uom(move.product_uom),
                            "pack": move.product_uom != move.product_id.uom_id,
                        }
                        for move in picking.move_ids
                    ],
                }
            )
        return result

    @api.model
    def desk_dispute_resolve(self, bar_id, token, uuid, picking_id, accept):
        """Accept a dispute (the goods never left: validate the correction)
        or reject it (cancel it: the variance stays with the bar)."""
        store, employee = self._desk_store(bar_id, token)
        replay = self._desk_replay(uuid)
        if replay:
            return self._desk_result(replay)
        picking = self.env["stock.picking"].sudo().browse(int(picking_id or 0)).exists()
        if not picking or not picking.filtered_domain(store._desk_dispute_domain()):
            raise UserError(_("This dispute is not open at %(store)s.", store=store.name))
        other = picking.location_id if picking.location_dest_id == store.location_id else picking.location_dest_id
        activity = self._desk_begin(
            uuid,
            {
                "kind": "dispute_accept" if accept else "dispute_reject",
                "bar_id": store.id,
                "dest_bar_id": self.env["odin.bar"].sudo().search([("location_id", "=", other.id)], limit=1).id,
                "employee_id": employee.id,
                "business_date": store._business_date(),
                "source_picking_id": picking.bar_dispute_origin_id.id,
                "summary": picking.name,
            },
        )
        if accept:
            self._desk_validate(picking)
        else:
            picking.action_cancel()
        return self._desk_result(activity)
