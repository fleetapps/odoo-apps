import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_date

MANAGER_GROUP = "odin_bar_desk.group_bar_desk_manager"


class OdinBarCount(models.Model):
    """A blind stock count taken at a bar or the store.

    A closing count covers every product on the bar's count sheet at the end
    of a trading day; a spot count covers a few products at any time. Staff
    never see expected quantities. On approval the expected quantity is worked
    out as it stood when the count was submitted, and only the difference is
    posted, so stock that moved after the count (the next morning's delivery,
    say) is kept.
    """

    _name = "odin.bar.count"
    _description = "Bar Count"
    _inherit = ["mail.thread"]
    _order = "business_date desc, submitted_at desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    bar_id = fields.Many2one(
        "odin.bar", required=True, index=True, readonly=True, check_company=True
    )
    company_id = fields.Many2one(related="bar_id.company_id", store=True, index=True)
    kind = fields.Selection(
        [("closing", "Closing count"), ("spot", "Spot count")],
        required=True,
        default="closing",
        readonly=True,
    )
    business_date = fields.Date("Trading day", required=True, readonly=True, index=True)
    employee_id = fields.Many2one("hr.employee", string="Counted by", readonly=True)
    state = fields.Selection(
        [
            ("draft", "Counting"),
            ("submitted", "Submitted"),
            ("recount", "Recount requested"),
            ("approved", "Approved"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        required=True,
        readonly=True,
        index=True,
        tracking=True,
    )
    started_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    submitted_at = fields.Datetime(readonly=True, tracking=True)
    approved_by_id = fields.Many2one("res.users", string="Approved by", readonly=True)
    approved_at = fields.Datetime(readonly=True)
    replaced_by_id = fields.Many2one("odin.bar.count", string="Replaced by", readonly=True)
    line_ids = fields.One2many("odin.bar.count.line", "count_id", string="Lines")
    move_ids = fields.One2many("stock.move", "bar_count_id", string="Adjustments", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    line_count = fields.Integer("Line count", compute="_compute_totals")
    counted_count = fields.Integer("Counted lines", compute="_compute_totals")
    diff_value = fields.Monetary(
        "Variance",
        compute="_compute_diff_value",
        store=True,
        currency_field="currency_id",
        help="Value of the posted differences at cost. Negative means stock is missing.",
    )
    approve_blocked_reason = fields.Char(compute="_compute_approve_blocked_reason")
    preview_ready = fields.Boolean(compute="_compute_approve_blocked_reason")
    note = fields.Text("Manager note")

    _one_approved_closing = models.UniqueIndex(
        "(bar_id, business_date) WHERE state = 'approved' AND kind = 'closing'",
        "Only one closing count can be approved per bar and trading day.",
    )
    _one_draft_closing = models.UniqueIndex(
        "(bar_id, business_date) WHERE state = 'draft' AND kind = 'closing'",
        "A closing count for this bar and trading day is already in progress.",
    )

    @api.depends("bar_id", "kind", "business_date", "started_at")
    def _compute_name(self):
        for count in self:
            if count.kind == "spot" and count.started_at and count.bar_id:
                local = pytz.utc.localize(count.started_at).astimezone(count.bar_id._bar_tz())
                label = _("Spot %(time)s", time=local.strftime("%H:%M"))
            else:
                label = _("Close")
            count.name = f"{count.bar_id.code or ''} {label} {count.business_date or ''}"

    @api.depends("line_ids.touched")
    def _compute_totals(self):
        for count in self:
            count.line_count = len(count.line_ids)
            count.counted_count = len(count.line_ids.filtered("touched"))

    @api.depends("line_ids.diff_value")
    def _compute_diff_value(self):
        for count in self:
            count.diff_value = sum(count.line_ids.mapped("diff_value"))

    @api.depends("state", "kind", "bar_id", "business_date", "submitted_at")
    def _compute_approve_blocked_reason(self):
        for count in self:
            waiting = count.state in ("submitted", "recount")
            count.approve_blocked_reason = waiting and count._approve_blocked_reason()
            count.preview_ready = waiting and count._pos_ready()

    def _pos_ready(self):
        self.ensure_one()
        return self.kind != "closing" or self.bar_id._pos_day_posted(self.business_date)

    def _approve_blocked_reason(self):
        """Why this count cannot be approved yet, or False."""
        self.ensure_one()
        if not self._pos_ready():
            return _(
                "Post the POS import for %(bar)s on %(day)s before approving this count.",
                bar=self.bar_id.name,
                day=format_date(self.env, self.business_date, date_format="EEE d MMM"),
            )
        earlier = self._earlier_pending()
        if earlier:
            return _(
                "Approve or cancel %(count)s first: counts are approved in the order they were taken.",
                count=earlier.name,
            )
        return False

    def _earlier_pending(self):
        """Older counts of the same bar still waiting for a decision. Approving
        out of order would post their difference twice."""
        self.ensure_one()
        return self.sudo().search(
            [
                ("bar_id", "=", self.bar_id.id),
                ("state", "in", ("submitted", "recount")),
                ("submitted_at", "<", self.submitted_at),
                ("id", "!=", self.id),
            ],
            order="submitted_at",
            limit=1,
        )

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_("Only Bar Desk managers can do this."))

    def action_approve(self):
        self._check_manager()
        for count in self.sorted(lambda c: (c.submitted_at or c.create_date, c.id)):
            if count.state not in ("submitted", "recount"):
                raise UserError(_("%(count)s is not waiting for approval.", count=count.name))
            reason = count._approve_blocked_reason()
            if reason:
                raise UserError(reason)
            count.sudo()._post_adjustments()
        return True

    def _post_adjustments(self):
        """Post counted minus expected, as it stood when the count was submitted."""
        self.ensure_one()
        bar = self.bar_id
        company = bar.company_id
        lines = self.line_ids.filtered("touched")
        expected = bar._stock_as_of(
            lines.product_id, self.submitted_at, self.business_date, closing=self.kind == "closing"
        )
        fallback_location = self.env["stock.location"].search(
            [("usage", "=", "inventory"), ("company_id", "in", [company.id, False])], limit=1
        )
        move_vals = []
        for line in lines:
            product = line.product_id.with_company(company)
            uom = product.uom_id
            expected_qty = expected.get(product.id, 0.0)
            diff = uom.round(line.counted_qty - expected_qty)
            cost = product.standard_price
            line.write(
                {
                    "expected_qty": expected_qty,
                    "diff_qty": diff,
                    "unit_cost": cost,
                    "diff_value": company.currency_id.round(diff * cost),
                }
            )
            if uom.is_zero(diff):
                continue
            inventory_location = product.property_stock_inventory or fallback_location
            if diff > 0:
                source, destination = inventory_location, bar.location_id
            else:
                source, destination = bar.location_id, inventory_location
            move_vals.append(
                {
                    "product_id": product.id,
                    "product_uom": uom.id,
                    "product_uom_qty": abs(diff),
                    "company_id": company.id,
                    "state": "confirmed",
                    "location_id": source.id,
                    "location_dest_id": destination.id,
                    "is_inventory": True,
                    "inventory_name": self.name,
                    "picked": True,
                    "bar_count_id": self.id,
                    "move_line_ids": [
                        (
                            0,
                            0,
                            {
                                "product_id": product.id,
                                "product_uom_id": uom.id,
                                "quantity": abs(diff),
                                "location_id": source.id,
                                "location_dest_id": destination.id,
                                "company_id": company.id,
                            },
                        )
                    ],
                }
            )
        moves = self.env["stock.move"].create(move_vals)
        moves._action_done()
        if moves:
            # Date the adjustment when the count was taken, so later counts see
            # it in their own "as of" position whatever order they are approved in.
            moves.write({"date": self.submitted_at})
            moves.move_line_ids.write({"date": self.submitted_at})
        self.write(
            {"state": "approved", "approved_by_id": self.env.uid, "approved_at": fields.Datetime.now()}
        )

    def action_request_recount(self):
        self._check_manager()
        if any(count.state != "submitted" for count in self):
            raise UserError(_("Only submitted counts can be sent back for a recount."))
        self.write({"state": "recount"})
        return True

    def action_cancel(self):
        self._check_manager()
        if any(count.state == "approved" for count in self):
            raise UserError(_("An approved count cannot be cancelled. Count again instead."))
        self.write({"state": "cancel"})
        return True


class OdinBarCountLine(models.Model):
    _name = "odin.bar.count.line"
    _description = "Bar Count Line"
    _order = "sequence, id"

    count_id = fields.Many2one("odin.bar.count", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="count_id.company_id", store=True)
    bar_id = fields.Many2one(related="count_id.bar_id", store=True, index=True)
    business_date = fields.Date(related="count_id.business_date", store=True)
    state = fields.Selection(related="count_id.state")
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one("product.product", required=True, index=True)
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    touched = fields.Boolean("Entered", help="Staff entered a quantity, zero included.")
    unit_qty = fields.Float(
        "Units", digits="Product Unit", help="Items counted in the stock unit, e.g. beers."
    )
    bottle_detail = fields.Json(
        "Full bottles by size", help="Unit id: number of full bottles of that size."
    )
    full_bottles = fields.Float(
        "Full bottles", compute="_compute_full", store=True, digits="Product Unit"
    )
    full_qty = fields.Float(
        "Full bottles (stock unit)",
        compute="_compute_full",
        store=True,
        digits="Product Unit",
        help="The full bottles expressed in the stock unit, e.g. tots.",
    )
    open_tots = fields.Float(
        "Open bottle", digits="Product Unit", help="What is left in the open bottle, in tots."
    )
    counted_qty = fields.Float(
        "Counted", compute="_compute_counted_qty", store=True, digits="Product Unit"
    )
    counted_display = fields.Char("Counted as", compute="_compute_counted_display")
    expected_qty = fields.Float("Expected", readonly=True, digits="Product Unit")
    diff_qty = fields.Float("Difference", readonly=True, digits="Product Unit")
    unit_cost = fields.Float("Unit cost", readonly=True, digits="Product Price")
    diff_value = fields.Monetary("Variance", readonly=True, currency_field="currency_id")
    currency_id = fields.Many2one(related="count_id.currency_id")
    preview_expected_qty = fields.Float(
        "Expected (preview)", compute="_compute_preview", digits="Product Unit"
    )
    preview_diff_qty = fields.Float(
        "Difference (preview)", compute="_compute_preview", digits="Product Unit"
    )
    preview_diff_value = fields.Monetary(
        "Variance (preview)", compute="_compute_preview", currency_field="currency_id"
    )

    _count_product_uniq = models.Constraint(
        "UNIQUE(count_id, product_id)", "A product can only be counted once per count."
    )

    @api.depends("bottle_detail", "product_id")
    def _compute_full(self):
        Uom = self.env["uom.uom"]
        for line in self:
            detail = line.bottle_detail or {}
            full_bottles = full_qty = 0.0
            for uom in Uom.browse([int(key) for key in detail]).exists():
                bottles = float(detail.get(str(uom.id)) or 0.0)
                full_bottles += bottles
                full_qty += uom._compute_quantity(bottles, line.product_id.uom_id, round=False)
            line.full_bottles = full_bottles
            line.full_qty = line.product_id.uom_id.round(full_qty) if line.product_id else full_qty

    @api.depends("unit_qty", "full_qty", "open_tots")
    def _compute_counted_qty(self):
        for line in self:
            line.counted_qty = line.unit_qty + line.full_qty + line.open_tots

    @api.depends("touched", "unit_qty", "bottle_detail", "open_tots", "product_id")
    def _compute_counted_display(self):
        Uom = self.env["uom.uom"]
        for line in self:
            if not line.touched:
                line.counted_display = False
                continue
            parts = []
            for uom in Uom.browse([int(key) for key in (line.bottle_detail or {})]).exists():
                bottles = float(line.bottle_detail.get(str(uom.id)) or 0.0)
                if bottles:
                    parts.append(f"{bottles:g} × {uom.name}")
            if line.open_tots:
                parts.append(_("%(qty)s open", qty=f"{line.open_tots:g}"))
            if line.unit_qty or not parts:
                parts.append(f"{line.unit_qty:g}")
            line.counted_display = " + ".join(parts)

    @api.depends("count_id.state", "counted_qty")
    def _compute_preview(self):
        for count, lines in self.grouped("count_id").items():
            if count.state == "approved":
                for line in lines:
                    line.preview_expected_qty = line.expected_qty
                    line.preview_diff_qty = line.diff_qty
                    line.preview_diff_value = line.diff_value
                continue
            expected = {}
            if count.state in ("submitted", "recount") and count._pos_ready():
                expected = count.bar_id.sudo()._stock_as_of(
                    lines.product_id,
                    count.submitted_at,
                    count.business_date,
                    closing=count.kind == "closing",
                )
            for line in lines:
                if line.product_id.id in expected and line.touched:
                    product = line.product_id.with_company(count.company_id)
                    line.preview_expected_qty = expected[product.id]
                    line.preview_diff_qty = product.uom_id.round(
                        line.counted_qty - line.preview_expected_qty
                    )
                    line.preview_diff_value = line.preview_diff_qty * product.standard_price
                else:
                    line.preview_expected_qty = line.preview_diff_qty = line.preview_diff_value = 0.0
