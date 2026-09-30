import pytz

from odoo import _, api, fields, models


class OdinBarRequest(models.Model):
    """A bar asking the Main Store, or another bar, for stock.

    Asking moves nothing. The location asked sends what it has from its own
    Desk; that becomes an ordinary transfer, which shows at the asking bar in
    Stock in. What it did not have can be asked of another location.
    """

    _name = "odin.bar.request"
    _description = "Bar Stock Request"
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    bar_id = fields.Many2one("odin.bar", string="Asked by", required=True, index=True, readonly=True)
    source_bar_id = fields.Many2one("odin.bar", string="Asked of", required=True, index=True, readonly=True)
    company_id = fields.Many2one(related="bar_id.company_id", store=True, index=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    business_date = fields.Date("Trading day", readonly=True)
    employee_id = fields.Many2one("hr.employee", string="Asked by staff", readonly=True)
    note = fields.Char(readonly=True)
    state = fields.Selection(
        [
            ("open", "Waiting"),
            ("sent", "Sent"),
            ("partial", "Part sent"),
            ("none", "Not available"),
            ("cancel", "Cancelled"),
        ],
        default="open",
        required=True,
        index=True,
        readonly=True,
    )
    answered_by_id = fields.Many2one("hr.employee", string="Answered by", readonly=True)
    answered_at = fields.Datetime(readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Transfer", readonly=True)
    passed_from_id = fields.Many2one(
        "odin.bar.request", string="Passed on from", readonly=True, index="btree_not_null"
    )
    passed_to_ids = fields.One2many("odin.bar.request", "passed_from_id", string="Passed on to")
    line_ids = fields.One2many("odin.bar.request.line", "request_id", string="Items", readonly=True)
    missing_count = fields.Integer("Items not sent", compute="_compute_missing_count")

    @api.depends("bar_id", "source_bar_id", "date")
    def _compute_name(self):
        for request in self:
            when = ""
            if request.date and request.bar_id:
                when = pytz.utc.localize(request.date).astimezone(request.bar_id._bar_tz()).strftime("%d %b %H:%M")
            request.name = f"{request.bar_id.code or ''} → {request.source_bar_id.code or ''} {when}".strip()

    @api.depends("line_ids.missing_qty")
    def _compute_missing_count(self):
        for request in self:
            request.missing_count = len(request.line_ids.filtered(lambda line: line.missing_qty > 0))


class OdinBarRequestLine(models.Model):
    _name = "odin.bar.request.line"
    _description = "Bar Stock Request Line"
    _order = "id"

    request_id = fields.Many2one("odin.bar.request", required=True, index=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    uom_id = fields.Many2one("uom.uom", string="Unit", required=True)
    qty = fields.Float("Asked", digits="Product Unit")
    sent_qty = fields.Float("Sent", digits="Product Unit")
    missing_qty = fields.Float("Not sent", compute="_compute_missing_qty", store=True, digits="Product Unit")

    @api.depends("qty", "sent_qty", "request_id.state")
    def _compute_missing_qty(self):
        for line in self:
            answered = line.request_id.state in ("sent", "partial", "none")
            line.missing_qty = max(0.0, line.qty - line.sent_qty) if answered else 0.0

    def _display_qty(self, qty):
        self.ensure_one()
        unit = self.uom_id.name.split("(")[0].strip() or self.uom_id.name
        return _("%(qty)s × %(unit)s", qty=f"{qty:g}", unit=unit)
