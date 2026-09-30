from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL

from odoo.addons.base.models.res_partner import _tz_get


class OdinBar(models.Model):
    _name = "odin.bar"
    _description = "Bar"
    _order = "sequence, name, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        help="Short code such as BE. Used to find this bar's own operation "
        "types (ISS-BE, RET-BE, SAL-BE).",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    kind = fields.Selection(
        [("bar", "Bar"), ("store", "Store")],
        required=True,
        default="bar",
        help="A store issues stock to bars and receives from suppliers.",
    )
    location_id = fields.Many2one(
        "stock.location",
        required=True,
        ondelete="restrict",
        check_company=True,
        domain="[('usage', '=', 'internal')]",
    )
    warehouse_id = fields.Many2one(related="location_id.warehouse_id", store=True)
    store_id = fields.Many2one(
        "odin.bar",
        string="Store",
        check_company=True,
        domain="[('kind', '=', 'store')]",
        help="Store this bar is issued from and returns to.",
    )

    # Operation types that belong to this bar. Shared types (ROMA, EVT, DEBT,
    # IBT) are chosen where they are used, not here.
    issue_type_id = fields.Many2one(
        "stock.picking.type",
        string="Issue type",
        check_company=True,
        help="Store to this bar, e.g. ISS-BE.",
    )
    return_type_id = fields.Many2one(
        "stock.picking.type",
        string="Return type",
        check_company=True,
        help="This bar back to the store, e.g. RET-BE.",
    )
    sale_type_id = fields.Many2one(
        "stock.picking.type",
        string="POS sales type",
        check_company=True,
        domain="[('code', '=', 'outgoing')]",
        help="Sales out of this bar, posted by the POS importer, e.g. SAL-BE.",
    )
    receipt_type_id = fields.Many2one(
        "stock.picking.type",
        string="Receipt type",
        check_company=True,
        help="Supplier receipts into the store, e.g. IN.",
    )
    analytic_account_id = fields.Many2one("account.analytic.account", check_company=True)

    # POS mapping, used by the POS importer.
    partner_id = fields.Many2one(
        "res.partner",
        string="POS delivery contact",
        check_company=True,
        help="Delivery address of this bar's daily POS sale order, e.g. the "
        "club's Bulls Eye contact.",
    )
    pos_group_name = fields.Char(
        "POS group name",
        help="Name of this bar's group exactly as the POS report prints it, e.g. Bulls Eye.",
    )
    pos_suffix = fields.Char(
        "POS suffix",
        help="Code the POS adds to every item of this bar, without brackets: "
        "BE for 'Campari(BE)'.",
    )
    pos_required = fields.Boolean(
        "POS import required",
        default=True,
        help="Closing counts can only be approved once the POS import for "
        "that trading day is posted.",
    )
    pos_day_ids = fields.One2many("odin.bar.pos.day", "bar_id", string="POS days")
    pos_start_date = fields.Date(
        "POS days from",
        copy=False,
        help="First trading day of this bar's POS imports, set by the first import. "
        "Counts wait for every POS day from here on; move it later to leave "
        "earlier days out.",
    )

    # Trading day
    tz = fields.Selection(
        _tz_get,
        string="Timezone",
        required=True,
        default=lambda self: self.env.user.tz or "Africa/Nairobi",
    )
    day_start_hour = fields.Float(
        "Trading day starts at",
        default=6.0,
        help="Anything before this time belongs to the previous trading day, "
        "so a count at 01:00 is the closing count for the day before.",
    )
    setup_issues = fields.Text(compute="_compute_setup_issues")

    _code_company_uniq = models.Constraint(
        "UNIQUE(code, company_id)", "Bar codes must be unique per company."
    )
    _location_uniq = models.Constraint(
        "UNIQUE(location_id)", "A stock location can belong to one bar only."
    )
    _pos_group_uniq = models.Constraint(
        "UNIQUE(company_id, pos_group_name)", "Each POS group can belong to one bar only."
    )
    _pos_suffix_uniq = models.Constraint(
        "UNIQUE(company_id, pos_suffix)", "Each POS suffix can belong to one bar only."
    )
    _day_start_hour_range = models.Constraint(
        "CHECK(day_start_hour >= 0 AND day_start_hour < 24)",
        "The trading day must start between 00:00 and 23:59.",
    )

    @api.constrains("location_id")
    def _check_location_usage(self):
        for bar in self:
            if bar.location_id.usage != "internal":
                raise ValidationError(
                    _("%(bar)s: the location must be an internal location.", bar=bar.name)
                )

    @api.constrains("sale_type_id")
    def _check_sale_type(self):
        for bar in self:
            if bar.sale_type_id and bar.sale_type_id.code != "outgoing":
                raise ValidationError(
                    _("%(bar)s: the POS sales type must be a delivery operation.", bar=bar.name)
                )

    @api.depends(
        "kind",
        "store_id",
        "issue_type_id",
        "return_type_id",
        "sale_type_id",
        "receipt_type_id",
        "pos_required",
    )
    def _compute_setup_issues(self):
        for bar in self:
            issues = []
            if bar.kind == "bar":
                if not bar.store_id:
                    issues.append(_("No store: returns have nowhere to go."))
                if not bar.issue_type_id:
                    issues.append(_("No issue type: the store cannot send to this bar."))
                if not bar.return_type_id:
                    issues.append(_("No return type: 'Back to store' and disputes are blocked."))
                if bar.pos_required and not bar.sale_type_id:
                    issues.append(_("No POS sales type for the POS importer."))
            elif not bar.receipt_type_id:
                issues.append(_("No receipt type: supplier receipts are blocked."))
            bar.setup_issues = "\n".join(issues) or False

    @api.onchange("kind")
    def _onchange_kind(self):
        if self.kind == "store":
            self.store_id = False
            self.pos_required = False

    def action_autofill_setup(self):
        """Fill empty settings from the naming the stock setup already uses:
        ISS-<code>, RET-<code>, SAL-<code>, the store's receipt type, and an
        analytic account named like the bar."""
        PickingType = self.env["stock.picking.type"]
        for bar in self:
            company_domain = [("company_id", "=", bar.company_id.id)]
            vals = {}
            if bar.kind == "bar":
                for field, pattern in (
                    ("issue_type_id", "ISS-%s"),
                    ("return_type_id", "RET-%s"),
                    ("sale_type_id", "SAL-%s"),
                ):
                    if not bar[field]:
                        ptype = PickingType.search(
                            company_domain + [("sequence_code", "=", pattern % bar.code)], limit=1
                        )
                        if ptype:
                            vals[field] = ptype.id
                if not bar.store_id:
                    store = self.search(company_domain + [("kind", "=", "store")], limit=1)
                    if store:
                        vals["store_id"] = store.id
            elif not bar.receipt_type_id:
                ptype = PickingType.search(
                    company_domain
                    + [
                        ("code", "=", "incoming"),
                        ("default_location_dest_id", "=", bar.location_id.id),
                    ],
                    limit=1,
                )
                if ptype:
                    vals["receipt_type_id"] = ptype.id
            if not bar.analytic_account_id:
                account = self.env["account.analytic.account"].search(
                    [("company_id", "in", [bar.company_id.id, False]), ("name", "=ilike", bar.name)],
                    limit=1,
                )
                if account:
                    vals["analytic_account_id"] = account.id
            if vals:
                bar.write(vals)
        return True

    # ------------------------------------------------------------------
    # Trading day
    # ------------------------------------------------------------------

    def _bar_tz(self):
        self.ensure_one()
        return pytz.timezone(self.tz or "UTC")

    def _business_date(self, at=None):
        """Trading day that the UTC datetime ``at`` (default: now) falls in."""
        self.ensure_one()
        local = pytz.utc.localize(at or fields.Datetime.now()).astimezone(self._bar_tz())
        return (local - timedelta(hours=self.day_start_hour)).date()

    def _business_day_start(self, day):
        """UTC datetime (naive) at which trading day ``day`` starts."""
        self.ensure_one()
        start = datetime.combine(day, time()) + timedelta(hours=self.day_start_hour)
        return self._bar_tz().localize(start).astimezone(pytz.utc).replace(tzinfo=None)

    def _business_day_bounds(self, day):
        self.ensure_one()
        return self._business_day_start(day), self._business_day_start(day + timedelta(days=1))

    # ------------------------------------------------------------------
    # POS import
    #
    # Stock is the one place quantities live. A POS importer posts each
    # trading day's sales into it after the day is over, so it also records
    # two facts stock cannot hold: which trading day each sales delivery
    # belongs to (``stock.picking.bar_business_date``) and which days are in
    # (``odin.bar.pos.day``, through the methods below).
    # ------------------------------------------------------------------

    def _pos_tracked(self):
        """Whether this bar's stock waits for POS imports."""
        self.ensure_one()
        return self.kind == "bar" and self.pos_required

    def _pos_missing_day(self, day):
        """First trading day up to ``day`` whose POS sales are not posted yet,
        or False when they all are.

        Days are checked from the bar's first POS day on (``pos_start_date``),
        since the sales of a day imported late would change the stock under a
        count already approved. A bar without any POS day is missing ``day``.
        """
        self.ensure_one()
        if not self._pos_tracked():
            return False
        PosDay = self.env["odin.bar.pos.day"].sudo()
        start = self.pos_start_date or PosDay.search(
            [("bar_id", "=", self.id)], order="business_date", limit=1
        ).business_date
        if not start or start > day:
            return day
        posted = set(
            PosDay.search_fetch(
                [
                    ("bar_id", "=", self.id),
                    ("business_date", ">=", start),
                    ("business_date", "<=", day),
                ],
                ["business_date"],
            ).mapped("business_date")
        )
        current = start
        while current <= day:
            if current not in posted:
                return current
            current += timedelta(days=1)
        return False

    def _pos_day_posted(self, day):
        """Whether the POS sales of every trading day up to ``day`` are in stock."""
        self.ensure_one()
        return not self._pos_missing_day(day)

    def _mark_pos_posted(self, day, source=False, no_sales=False):
        """Record that the POS sales of ``day`` are posted to this bar's stock.

        The hook for a POS importer: call it for every bar of the report once
        the day's sales deliveries are validated and carry
        ``bar_business_date``, with ``no_sales=True`` for a bar the report has
        no sales for. Calling it again for the same day updates the record.
        """
        self.ensure_one()
        PosDay = self.env["odin.bar.pos.day"].sudo()
        vals = {"source": source, "no_sales": no_sales}
        pos_day = PosDay.search([("bar_id", "=", self.id), ("business_date", "=", day)])
        if pos_day:
            pos_day.write(vals)
        else:
            pos_day = PosDay.create(dict(vals, bar_id=self.id, business_date=day))
        if not self.pos_start_date or day < self.pos_start_date:
            self.sudo().pos_start_date = day
        if not no_sales:
            self._pos_day_changed(day)
        return pos_day

    def _unmark_pos_posted(self, day):
        """Forget that ``day`` is posted, once its sales are taken back out of
        stock (a POS import undone). Counts from that day on wait again, even
        for the bar's first POS day."""
        self.ensure_one()
        pos_days = (
            self.env["odin.bar.pos.day"]
            .sudo()
            .with_context(odin_bar_pos_unmark=True)
            .search([("bar_id", "=", self.id), ("business_date", "=", day)])
        )
        had_sales = any(not pos_day.no_sales for pos_day in pos_days)
        pos_days.unlink()
        if had_sales:
            self._pos_day_changed(day)
        return bool(pos_days)

    def _pos_day_changed(self, day):
        """Hook: POS sales of ``day`` changed this bar's stock after the fact
        (posted, or taken back out). A module that approves stock counts
        re-opens the counts of that day and later ones."""
        return True

    def _pos_day_dependents(self, day):
        """Hook: settled records that rely on this bar's POS sales of ``day``
        and would be re-opened if they changed, as links for the importer's
        screen: dicts with ``model``, ``id``, ``name`` and ``extra``."""
        return []

    def _pos_day_waiting(self, day):
        """Hook: records that can go ahead now that ``day`` is posted at this
        bar, as links (see ``_pos_day_dependents``)."""
        return []

    def _pos_import_action(self, day):
        """Hook: the action that imports the POS sales of ``day``, or False.
        A POS importer returns its import screen."""
        return False

    def _pos_moved_qty(self, products, day):
        """Net quantity of ``products`` that left this bar through transfers of
        trading day ``day`` (POS sales, less their returns), in each product's
        unit. Kits appear as their components.

        :return: dict {product_id: quantity}
        """
        self.ensure_one()
        result = dict.fromkeys(products.ids, 0.0)
        if not products:
            return result
        location_ids = self.env["stock.location"].sudo().search(
            [("id", "child_of", self.location_id.id)]
        ).ids
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                """
                SELECT move.product_id,
                       SUM(CASE WHEN ml.location_id = ANY(%(locations)s)
                                THEN ml.quantity_product_uom ELSE 0 END)
                     - SUM(CASE WHEN ml.location_dest_id = ANY(%(locations)s)
                                THEN ml.quantity_product_uom ELSE 0 END)
                  FROM stock_move_line ml
                  JOIN stock_move move ON move.id = ml.move_id
                  JOIN stock_picking picking ON picking.id = move.picking_id
                 WHERE move.state = 'done'
                   AND picking.bar_business_date = %(day)s
                   AND move.product_id = ANY(%(products)s)
                   AND (ml.location_id = ANY(%(locations)s))
                       <> (ml.location_dest_id = ANY(%(locations)s))
              GROUP BY move.product_id
                """,
                locations=location_ids,
                products=products.ids,
                day=day,
            )
        )
        for product_id, qty in self.env.cr.fetchall():
            result[product_id] = qty or 0.0
        for product in products:
            result[product.id] = product.uom_id.round(result[product.id])
        return result

    # ------------------------------------------------------------------
    # Stock position
    # ------------------------------------------------------------------

    def _stock_as_of(self, products, cutoff, business_date, closing=True):
        """Quantity of ``products`` at this bar, in each product's UoM, as it
        stood at the UTC datetime ``cutoff`` during trading day ``business_date``.

        Movements that carry a trading day (``bar_business_date``, set by the
        POS importer on sales) are placed by that day, not by posting time,
        because sales are imported after the fact:

        - earlier trading days always count;
        - ``business_date`` itself counts in full for a closing count, since
          the whole day's sales happened before the close, and only up to
          ``cutoff`` for a spot count;
        - later trading days never count.

        Every other movement counts when it was done at or before ``cutoff``.

        :return: dict {product_id: quantity}
        """
        self.ensure_one()
        result = dict.fromkeys(products.ids, 0.0)
        if not products:
            return result
        location_ids = self.env["stock.location"].sudo().search(
            [("id", "child_of", self.location_id.id)]
        ).ids
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                """
                SELECT move.product_id,
                       SUM(CASE WHEN ml.location_dest_id = ANY(%(locations)s)
                                THEN ml.quantity_product_uom ELSE 0 END)
                     - SUM(CASE WHEN ml.location_id = ANY(%(locations)s)
                                THEN ml.quantity_product_uom ELSE 0 END)
                  FROM stock_move_line ml
                  JOIN stock_move move ON move.id = ml.move_id
             LEFT JOIN stock_picking picking ON picking.id = move.picking_id
                 WHERE move.state = 'done'
                   AND move.product_id = ANY(%(products)s)
                   AND (ml.location_id = ANY(%(locations)s))
                       <> (ml.location_dest_id = ANY(%(locations)s))
                   AND CASE
                       WHEN picking.bar_business_date IS NULL THEN move.date <= %(cutoff)s
                       WHEN picking.bar_business_date < %(day)s THEN TRUE
                       WHEN picking.bar_business_date = %(day)s
                           THEN %(closing)s OR move.date <= %(cutoff)s
                       ELSE FALSE
                   END
              GROUP BY move.product_id
                """,
                locations=location_ids,
                products=products.ids,
                cutoff=cutoff,
                day=business_date,
                closing=bool(closing),
            )
        )
        for product_id, qty in self.env.cr.fetchall():
            result[product_id] = qty or 0.0
        for product in products:
            result[product.id] = product.uom_id.round(result[product.id])
        return result
