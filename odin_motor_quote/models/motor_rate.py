from odoo import _, api, fields, models
from odoo.tools import formatLang

COVERS = [("comprehensive", "Comprehensive"), ("tpo", "Third party only")]
OFFERED = [("na", "Not offered"), ("included", "Included"), ("optional", "Optional")]
#: what differs between the bands of one product
BAND_FIELDS = ("value_min", "value_max", "tonnage_min", "tonnage_max", "seats_min",
               "seats_max", "rate", "min_premium", "flat_premium")


class MotorRate(models.Model):
    """One line of an insurer's motor rate card.

    For a vehicle type and cover, and within a band -- of value, tonnage or
    passengers -- either a rate on the vehicle's value with a minimum
    premium, or a flat premium (third party only, often by period). Then
    what the insurer adds: political violence and terrorism (PVT), excess
    protector, passenger legal liability (PLL), courtesy car, and any charge
    that always comes with it.
    """

    _name = "odin.motor.rate"
    _description = "Motor rate"
    _order = "class_id, cover, insurer_id, value_min, tonnage_min, seats_min, period_months desc, id"

    insurer_id = fields.Many2one(
        "res.partner", string="Insurer", required=True, index=True,
        domain=[("is_company", "=", True)])
    class_id = fields.Many2one(
        "odin.motor.class", string="Vehicle type", required=True, index=True, ondelete="restrict")
    cover = fields.Selection(COVERS, required=True, default="comprehensive")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    active = fields.Boolean(default=True)

    value_min = fields.Monetary(
        string="Value from", help="Lowest vehicle value (sum insured) this rate takes.")
    value_max = fields.Monetary(
        string="Value up to", help="Highest vehicle value for this rate; empty for no limit.")
    max_age = fields.Integer(
        string="Vehicle age up to", help="Oldest vehicle, in years, the insurer takes; empty for any.")
    tonnage_min = fields.Float(string="Tonnage from")
    tonnage_max = fields.Float(string="Tonnage up to")
    seats_min = fields.Integer(string="Passengers from")
    seats_max = fields.Integer(string="Passengers up to")
    period_months = fields.Integer(
        string="Period (months)", default=12, required=True,
        help="Most cover is for a year; some third-party rates are for a month or six.")

    rate = fields.Float(
        string="Rate (%)", digits=(16, 4),
        help="Basic premium as a percentage of the vehicle's value.")
    min_premium = fields.Monetary(string="Minimum premium")
    flat_premium = fields.Monetary(
        string="Premium", help="A fixed basic premium, where there is no rate (third party only).")

    pvt = fields.Selection(OFFERED, string="Political violence (PVT)", default="na", required=True)
    pvt_rate = fields.Float(string="PVT rate (%)", digits=(16, 4))
    pvt_min = fields.Monetary(string="PVT minimum")
    pvt_amount = fields.Monetary(string="PVT amount", help="A fixed price, where there is no rate.")
    excess = fields.Selection(OFFERED, string="Excess protector", default="na", required=True)
    excess_rate = fields.Float(string="Excess protector rate (%)", digits=(16, 4))
    excess_min = fields.Monetary(string="Excess protector minimum")
    excess_amount = fields.Monetary(string="Excess protector amount")
    pll_amount = fields.Monetary(
        string="PLL per passenger", help="Passenger legal liability, per passenger.")
    pll_required = fields.Boolean(
        string="PLL required", help="Always charged (public service vehicles), not an option.")
    courtesy_10 = fields.Monetary(string="Courtesy car, 10 days")
    courtesy_20 = fields.Monetary(string="Courtesy car, 20 days")
    extra_label = fields.Char(
        string="Other charge", help="A charge that always comes with this rate, e.g. "
        "mandatory personal accident cover.")
    extra_amount = fields.Monetary(string="Other charge amount")
    note = fields.Text(
        string="Notes", help="Printed with the quote: conditions, excess, what is included.")
    product_id = fields.Many2one(
        "odin.cover.product", string="Product", index=True, ondelete="cascade",
        help="Cover Quotes ▸ Products: the insurer's cover this band belongs to. What every "
        "band of a product shares is kept on the product.")
    odin_removed = fields.Boolean(
        copy=False, help="Taken off its product after being quoted: kept for the quotes "
        "that used it, never offered again.")

    # -- belonging to a product ---------------------------------------------

    @api.model
    def _odin_shared_fields(self):
        return self.env["odin.cover.product"]._odin_motor_shared()

    def _odin_rate_shared_values(self):
        self.ensure_one()
        values = {}
        for name in self._odin_shared_fields():
            value = self[name]
            values[name] = value.id if isinstance(value, models.BaseModel) else value
        return values

    @api.model
    def _odin_product_for(self, values):
        """The product a band with these shared values belongs to: the one
        that already has them, or a new one."""
        Product = self.env["odin.cover.product"].with_context(active_test=False)
        domain = [("line", "=", "motor")]
        for name, value in values.items():
            domain.append((name, "=", value if value not in (None,) else False))
        product = Product.search(domain, order="active desc, id", limit=1)
        if product:
            return product
        return Product.with_context(odin_adopting=True, mail_create_nolog=True).create(
            dict(values, line="motor", active=True))

    @api.model_create_multi
    def create(self, vals_list):
        shared = self._odin_shared_fields()
        defaults = self.default_get(shared)
        for vals in vals_list:
            product = self.env["odin.cover.product"].browse(vals.get("product_id") or [])
            if product:
                # A band added on its product's form: the product says the rest.
                vals.update(product._odin_shared_values())
                if not product.active:
                    vals["active"] = False
            else:
                values = dict(defaults, **{k: vals[k] for k in shared if k in vals})
                values.setdefault("company_id", self.env.company.id)
                vals.update(values)
                vals["product_id"] = self._odin_product_for(values).id
        rates = super().create(vals_list)
        if not self.env.context.get("odin_adopting"):
            for rate in rates:
                rate.product_id._odin_log_rate(_("Band added: %s", rate._odin_band_summary()))
        return rates

    def write(self, vals):
        shared = set(self._odin_shared_fields()) & set(vals)
        logged = {}
        if set(BAND_FIELDS) & set(vals):
            logged = {rate.id: rate._odin_band_summary() for rate in self}
        result = super().write(vals)
        if shared and not self.env.context.get("odin_from_product"):
            # Edited band by band (the old Motor rates list): the band moves
            # to the product that has its new values.
            for rate in self:
                product = rate._odin_product_for(rate._odin_rate_shared_values())
                if product != rate.product_id:
                    super(MotorRate, rate).write({"product_id": product.id})
        for rate in self.filtered(lambda r: r.id in logged):
            after = rate._odin_band_summary()
            if after != logged[rate.id]:
                rate.product_id._odin_log_rate(_("Band changed: %(old)s → %(new)s",
                                                 old=logged[rate.id], new=after))
        return result

    def unlink(self):
        """A band already quoted is archived, not deleted: the quotations
        made from it keep pointing at it."""
        quoted = self.env["sale.order"].sudo().with_context(active_test=False).search(
            [("odin_motor_rate_id", "in", self.ids)]).odin_motor_rate_id
        for rate in self:
            rate.product_id._odin_log_rate(_("Band removed: %s", rate._odin_band_summary()))
        if quoted:
            quoted.write({"active": False, "odin_removed": True})
        return super(MotorRate, self - quoted).unlink()

    def _odin_band_summary(self):
        """``800,000 to 1,499,999: 4.5%, minimum 40,000`` / ``up to 3 t: 5,000``."""
        self.ensure_one()
        band = self._odin_band_text() or _("Any vehicle")
        if self.rate:
            price = self._odin_rate_text()
        else:
            price = self._odin_number(self.flat_premium)
        return "%s: %s" % (band, price)

    @api.depends("insurer_id", "class_id", "cover", "value_min", "value_max",
                 "tonnage_min", "tonnage_max", "seats_min", "seats_max", "period_months")
    def _compute_display_name(self):
        for rate in self:
            parts = [rate.insurer_id.name, rate.class_id.name,
                     dict(COVERS).get(rate.cover), rate._odin_band_text()]
            rate.display_name = " – ".join(p for p in parts if p)

    # -- reading the card ---------------------------------------------------

    def _odin_number(self, value):
        return formatLang(self.env, value, digits=0)

    def _odin_band_text(self):
        """``1,000,000 to 1,499,999`` / ``up to 3 t`` / ``10 to 17 passengers`` / ``1 month``."""
        self.ensure_one()
        parts = []

        def band(low, high, unit=""):
            if low and high:
                return _("%(low)s to %(high)s%(unit)s", low=self._odin_number(low),
                         high=self._odin_number(high), unit=unit)
            if high:
                return _("up to %(high)s%(unit)s", high=self._odin_number(high), unit=unit)
            if low:
                return _("%(low)s and above%(unit)s", low=self._odin_number(low), unit=unit)
            return ""

        parts.append(band(self.value_min, self.value_max))
        parts.append(band(self.tonnage_min, self.tonnage_max, " t"))
        parts.append(band(self.seats_min, self.seats_max, _(" passengers")))
        if self.period_months != 12:
            parts.append(_("1 month") if self.period_months == 1 else _("%s months", self.period_months))
        return ", ".join(p for p in parts if p)

    def _odin_rate_text(self, value=0.0):
        """How the basic premium is worked out, for the agent and the client."""
        self.ensure_one()
        if self.rate:
            text = _("%(rate)s%% of %(value)s", rate=("%g" % self.rate),
                     value=self._odin_number(value)) if value else _("%s%%", "%g" % self.rate)
            if self.min_premium:
                text = _("%(text)s, minimum %(min)s", text=text, min=self._odin_number(self.min_premium))
            return text
        return _("Fixed premium")

    def _odin_part(self, value, rate, minimum, amount):
        """A rate on the value with a minimum, or a fixed amount."""
        if rate:
            return max(value * rate / 100.0, minimum or 0.0)
        return amount or 0.0

    def _odin_basic(self, value):
        self.ensure_one()
        return self._odin_part(value, self.rate, self.min_premium, self.flat_premium)

    def _odin_pvt(self, value):
        return self._odin_part(value, self.pvt_rate, self.pvt_min, self.pvt_amount)

    def _odin_excess(self, value):
        return self._odin_part(value, self.excess_rate, self.excess_min, self.excess_amount)

    def _odin_fits(self, vehicle):
        """Whether this line of the card is for ``vehicle``'s period and
        bands. The value's lower bound is left to the engine (it picks the
        band the value falls in) and to _odin_refusal (when it is below
        every band), so the agent can be told the minimum."""
        self.ensure_one()

        def within(value, low, high):
            return (not low or value >= low) and (not high or value <= high)

        if self.period_months != vehicle["period"]:
            return False
        if self.cover == "comprehensive" and self.value_max and vehicle["value"] > self.value_max:
            return False
        if (self.tonnage_min or self.tonnage_max) and not within(
                vehicle["tonnage"], self.tonnage_min, self.tonnage_max):
            return False
        if (self.seats_min or self.seats_max) and not within(
                vehicle["seats"], self.seats_min, self.seats_max):
            return False
        return True

    def _odin_refusal(self, vehicle):
        """Why this insurer will not quote the vehicle, or ''."""
        self.ensure_one()
        if self.max_age and vehicle["age"] is not None and vehicle["age"] > self.max_age:
            return _("Vehicles up to %s years old.", self.max_age)
        if self.cover == "comprehensive" and self.value_min and vehicle["value"] < self.value_min:
            return _("Vehicles worth %s or more.", self._odin_number(self.value_min))
        return ""
