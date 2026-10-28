"""Motor products: an insurer's cover for one vehicle type, and its bands.

What stays the same for every band -- insurer, vehicle type, cover, period,
the extras -- is kept on the product and copied to each band, so the quote
engine goes on reading motor rates exactly as before. A product is edited in
one form; its bands are the rows of that form.
"""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import formatLang

from .motor_rate import COVERS, OFFERED

#: on the product, and the same on every band
SHARED = (
    "insurer_id", "class_id", "cover", "period_months", "company_id", "max_age",
    "pvt", "pvt_rate", "pvt_min", "pvt_amount",
    "excess", "excess_rate", "excess_min", "excess_amount",
    "pll_amount", "pll_required", "courtesy_10", "courtesy_20",
    "extra_label", "extra_amount", "note",
)


class CoverProduct(models.Model):
    _inherit = "odin.cover.product"

    line = fields.Selection(selection_add=[("motor", "Motor")], ondelete={"motor": "cascade"})
    class_id = fields.Many2one(
        "odin.motor.class", string="Vehicle type", ondelete="restrict", tracking=True)
    cover = fields.Selection(COVERS, default="comprehensive", tracking=True)
    period_months = fields.Integer(
        string="Period (months)", default=12, tracking=True,
        help="Most cover is for a year; some third-party rates are for a month or six.")
    max_age = fields.Integer(
        string="Vehicle age up to", tracking=True,
        help="Oldest vehicle, in years, the insurer takes; empty for any.")
    quote_label = fields.Char(
        string="Name on quotes",
        help="Shown beside the insurer when it has two products for the same vehicle, "
        "e.g. Premier.")
    rate_ids = fields.One2many("odin.motor.rate", "product_id", string="Bands", copy=True)

    pvt = fields.Selection(OFFERED, string="Political violence (PVT)", default="na")
    pvt_rate = fields.Float(string="PVT rate (%)", digits=(16, 4))
    pvt_min = fields.Monetary(string="PVT minimum")
    pvt_amount = fields.Monetary(string="PVT amount", help="A fixed price, where there is no rate.")
    excess = fields.Selection(OFFERED, string="Excess protector", default="na")
    excess_rate = fields.Float(string="Excess protector rate (%)", digits=(16, 4))
    excess_min = fields.Monetary(string="Excess protector minimum")
    excess_amount = fields.Monetary(string="Excess protector amount")
    pll_amount = fields.Monetary(string="PLL per passenger",
                                 help="Passenger legal liability, per passenger.")
    pll_required = fields.Boolean(
        string="PLL required", help="Always charged (public service vehicles), not an option.")
    courtesy_10 = fields.Monetary(string="Courtesy car, 10 days")
    courtesy_20 = fields.Monetary(string="Courtesy car, 20 days")
    extra_label = fields.Char(string="Other charge",
                              help="A charge that always comes with this cover.")
    extra_amount = fields.Monetary(string="Other charge amount")

    ask_tonnage = fields.Boolean(related="class_id.ask_tonnage")
    ask_seats = fields.Boolean(related="class_id.ask_seats")
    try_value = fields.Monetary(string="Vehicle value", default=1500000)
    try_tonnage = fields.Float(string="Tonnage", default=3)
    try_seats = fields.Integer(string="Passengers", default=14)
    try_year = fields.Integer(string="Year of manufacture")

    # -- what the product shares with its bands ----------------------------

    @api.model
    def _odin_motor_shared(self):
        return list(SHARED)

    def _odin_shared_values(self):
        self.ensure_one()
        values = {}
        for name in self._odin_motor_shared():
            value = self[name]
            values[name] = value.id if isinstance(value, models.BaseModel) else value
        return values

    @api.model
    def _odin_motor_name(self, values):
        """``Definite – Pick-up (commercial) – Third party only``."""
        insurer = self.env["res.partner"].browse(values.get("insurer_id") or [])
        vehicle = self.env["odin.motor.class"].browse(values.get("class_id") or [])
        cover = dict(COVERS).get(values.get("cover") or "comprehensive")
        parts = [insurer.name, vehicle.name, cover]
        period = values.get("period_months") or 12
        if period != 12:
            parts.append(_("1 month") if period == 1 else _("%s months", period))
        return " – ".join(p for p in parts if p)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("line") == "motor" and not vals.get("name"):
                vals["name"] = self._odin_motor_name(vals)
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        shared = set(self._odin_motor_shared()) & set(vals)
        motor = self.filtered(lambda p: p.line == "motor")
        if shared:
            for product in motor:
                values = {name: product._odin_shared_values()[name] for name in shared}
                product.rate_ids.with_context(active_test=False, odin_from_product=True).write(values)
        if "active" in vals:
            for product in motor:
                product.with_context(active_test=False).rate_ids.filtered(
                    lambda r: not r.odin_removed).with_context(odin_from_product=True).write(
                    {"active": vals["active"]})
        return result

    # -- reading ------------------------------------------------------------

    @api.model
    def _odin_summary_depends(self):
        return super()._odin_summary_depends() + ["class_id", "cover", "period_months"]

    def _odin_summary(self):
        if self.line != "motor":
            return super()._odin_summary()
        parts = [self.class_id.name, dict(COVERS).get(self.cover)]
        if self.period_months and self.period_months != 12:
            parts.append(_("1 month") if self.period_months == 1
                         else _("%s months", self.period_months))
        return " · ".join(p for p in parts if p)

    @api.model
    def _odin_rate_count_depends(self):
        return super()._odin_rate_count_depends() + ["rate_ids"]

    def _odin_rate_count(self):
        if self.line != "motor":
            return super()._odin_rate_count()
        return len(self.rate_ids)

    @api.model
    def _odin_check_depends(self):
        return super()._odin_check_depends() + [
            "class_id", "cover", "period_months", "rate_ids.value_min", "rate_ids.value_max",
            "rate_ids.tonnage_min", "rate_ids.tonnage_max", "rate_ids.seats_min",
            "rate_ids.seats_max", "rate_ids.rate", "rate_ids.min_premium", "rate_ids.flat_premium",
            "pvt", "pvt_rate", "excess", "excess_rate"]

    def _odin_warnings(self):
        warnings = super()._odin_warnings()
        if self.line == "motor":
            warnings += self._odin_motor_warnings()
        return warnings

    def _odin_motor_warnings(self):
        warnings = []
        money = lambda value: formatLang(self.env, value, digits=0)  # noqa: E731
        if not self.class_id:
            warnings.append(_("Choose the vehicle type."))
        rates = self.rate_ids
        if not rates:
            return warnings + [_("Add at least one rate.")]
        for rate in rates:
            if self.cover == "comprehensive":
                if not rate.rate and not rate.flat_premium:
                    warnings.append(_("A band has no rate."))
                if rate.rate > 20:
                    warnings.append(_("A rate of %(rate)s%% looks high: did you mean %(less)s%%?",
                                      rate="%g" % rate.rate, less="%g" % (rate.rate / 100)))
                if rate.rate and not rate.min_premium:
                    warnings.append(_("A band of %s%% has no minimum premium.", "%g" % rate.rate))
            elif not rate.flat_premium and not rate.rate:
                warnings.append(_("A band has no premium."))
        for low_field, high_field, unit, asked in (
                ("value_min", "value_max", "", self.cover == "comprehensive"),
                ("tonnage_min", "tonnage_max", " t", self.class_id.ask_tonnage),
                ("seats_min", "seats_max", _(" passengers"), self.class_id.ask_seats)):
            if not asked:
                continue
            bands = sorted((r[low_field] or 0, r[high_field] or 0) for r in rates)
            if len(bands) < 2:
                continue
            for (low, high), (next_low, _next_high) in zip(bands, bands[1:]):
                if not high:
                    warnings.append(_("Two bands start at %(low)s%(unit)s and have no upper limit.",
                                      low=money(low), unit=unit))
                elif next_low <= high:
                    warnings.append(_("Bands overlap at %(value)s%(unit)s.",
                                      value=money(next_low), unit=unit))
                elif next_low - high > 1:
                    warnings.append(_("No rate between %(high)s and %(low)s%(unit)s.",
                                      high=money(high), low=money(next_low), unit=unit))
        same = self.search([
            ("id", "!=", self._origin.id or 0), ("line", "=", "motor"),
            ("insurer_id", "=", self.insurer_id.id), ("class_id", "=", self.class_id.id),
            ("cover", "=", self.cover), ("period_months", "=", self.period_months),
            ("company_id", "=", self.company_id.id)])
        if same and self.insurer_id and self.class_id:
            warnings.append(_("%(insurer)s already has a product for this vehicle and cover: "
                              "%(other)s. Both will be quoted.",
                              insurer=self.insurer_id.name, other=same[0].name))
        return warnings

    @api.model
    def _odin_try_depends(self):
        return super()._odin_try_depends() + [
            "class_id", "cover", "period_months", "try_value", "try_tonnage", "try_seats",
            "try_year", "rate_ids.rate", "rate_ids.min_premium", "rate_ids.flat_premium",
            "rate_ids.value_min", "rate_ids.value_max", "rate_ids.tonnage_min",
            "rate_ids.tonnage_max", "rate_ids.seats_min", "rate_ids.seats_max",
            "pvt", "pvt_rate", "pvt_min", "pvt_amount", "excess", "excess_rate", "excess_min",
            "excess_amount", "pll_amount", "pll_required", "courtesy_10", "courtesy_20",
            "extra_amount", "max_age"]

    def _odin_try(self):
        """The vehicle typed in, priced by New quote's engine."""
        if self.line != "motor":
            return super()._odin_try()
        if not self._origin.id or not self.class_id:
            return Markup("<p class='text-muted'>%s</p>") % _(
                "Save the product with its rates to try it.")
        Quote = self.env["odin.motor.quote"]
        this_year = fields.Date.context_today(self).year
        vehicle = {
            "class": self.class_id,
            "cover": self.cover,
            "value": self.try_value if self.cover == "comprehensive" else 0.0,
            "age": max(this_year - self.try_year, 0) if self.try_year else None,
            "seats": self.try_seats if self.class_id.ask_seats else 0,
            "tonnage": self.try_tonnage if self.class_id.ask_tonnage else 0.0,
            "period": self.period_months or 12,
            "registration": "",
            "make_model": "",
        }
        rates = self._origin.rate_ids
        rate, reason = Quote._odin_pick_rate(rates, vehicle)
        if reason or not rate:
            return Markup("<p class='text-danger'>%s</p>") % (reason or _("No rate for these details."))
        offer = Quote._odin_motor_offer(rate, vehicle)
        premium_product = Quote._odin_product("premium")
        rows = [("%s (%s)" % (line.section, line.label) if line.label else line.section,
                 line.subtotal) for line in offer["core"]]
        premium = sum(line.subtotal for line in offer["core"])
        levies = self._odin_levies(premium_product, premium)
        charges = sum(line.subtotal for line in offer["charges"])
        rows += [(_("Levies"), levies), (_("Stamp duty"), charges)]
        html = self._odin_try_table(rows, _("Total"), premium + levies + charges)
        extras = [Markup("<li>%s: %s</li>") % (group["label"], " / ".join(
            "%s %s" % (o["name"], self._odin_money(o["amount"])) for o in group["options"]))
            for group in offer["groups"]]
        if extras:
            html += Markup("<p class='mt-2 mb-1'><b>%s</b></p><ul>%s</ul>") % (
                _("Extras the client can add"), Markup("").join(extras))
        return html

    def _odin_quoted(self):
        if self.line == "motor":
            rates = self.with_context(active_test=False).rate_ids
            return bool(rates and self.env["sale.order"].sudo().search_count(
                [("odin_motor_rate_id", "in", rates.ids)], limit=1))
        return super()._odin_quoted()

    def _odin_log_rate(self, body):
        for product in self:
            product.message_post(body=body)

    # -- taking over the rates already there ---------------------------------

    @api.model
    def _odin_adopt_rates(self):
        """Every motor rate made before Products existed is put on a
        product: rates that differ only in their band share one."""
        Rate = self.env["odin.motor.rate"].with_context(active_test=False)
        for rate in Rate.search([("product_id", "=", False)]):
            rate.product_id = rate._odin_product_for(rate._odin_rate_shared_values())
        return True
