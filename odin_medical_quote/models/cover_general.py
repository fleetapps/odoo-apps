"""Products of a class the office adds itself: Accident, Fire, Travel...

There is no engine to write for them. A product is one insurer's cover for
the class, priced the way most non-medical rate cards are:

* **bands** of the amount the class asks for (the sum insured, the annual
  wages, the limit...): within a band, a rate on the amount with a minimum
  premium, or a fixed premium. One band with no amounts applies to every
  quote; a class that asks no amount has one band with a fixed premium.
* **per person** or per policy, where the class asks how many people.
* **extras**, included or for the client to add: a rate on the amount with a
  minimum, or a fixed price, per person or per policy.

The standard quote screen (odin.general.quote) prices every product of the
class this way, side by side, as the motor screen prices every insurer's
rate for a vehicle.
"""

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang

#: a rate (%) on the amount that looks like the decimal point went missing
HIGH_RATE = 20.0


def money(env, value):
    return formatLang(env, value, digits=0)


class CoverProduct(models.Model):
    _inherit = "odin.cover.product"

    odin_engine = fields.Char(
        string="Quoted on", compute="_compute_odin_class_info",
        help="The screen the product's class is quoted on: medical, motor or general.")
    odin_ask_amount = fields.Boolean(compute="_compute_odin_class_info")
    odin_ask_people = fields.Boolean(compute="_compute_odin_class_info")
    odin_amount_label = fields.Char(compute="_compute_odin_class_info")

    period_months = fields.Integer(
        string="Period (months)", default=12, tracking=True,
        help="Most cover is for a year. A shorter period is not renewed.")
    quote_label = fields.Char(
        string="Name on quotes",
        help="What the quote calls this product beside the insurer, e.g. Personal "
        "Accident Plus. Left empty, the product's name.")
    per_person = fields.Boolean(
        string="Priced per person",
        help="The premium of each band is for one person, and is charged for each "
        "person covered.")
    benefits = fields.Text(
        string="What it pays out",
        help="Printed under the cover on the quotation, one per line, e.g. "
        "Death: 1,000,000.")
    band_ids = fields.One2many("odin.cover.band", "product_id", string="Premium bands", copy=True)
    extra_ids = fields.One2many("odin.cover.extra", "product_id", string="Extras", copy=True)
    try_amount = fields.Monetary(string="Amount", default=1000000)
    try_people = fields.Integer(string="People", default=1)

    @api.depends("line")
    def _compute_odin_class_info(self):
        classes = {c.code: c for c in self.env["odin.insurance.class"].sudo().with_context(
            active_test=False).search([("code", "in", list(set(self.mapped("line"))))])}
        for product in self:
            record = classes.get(product.line)
            product.odin_engine = record.engine if record else False
            product.odin_ask_amount = bool(record and record.ask_amount)
            product.odin_ask_people = bool(record and record.ask_people)
            product.odin_amount_label = record.amount_label if record else False

    def write(self, vals):
        if "line" in vals:
            engine = self.env["odin.insurance.class"]._odin_get(vals["line"]).engine
            if any(product.odin_engine and product.odin_engine != engine for product in self):
                raise UserError(_(
                    "A product cannot move to a class quoted on another screen. Make a "
                    "new product in that class instead."))
        return super().write(vals)

    def _odin_class(self):
        self.ensure_one()
        return self.env["odin.insurance.class"]._odin_get(self.line)

    def _odin_is_general(self):
        self.ensure_one()
        return self.odin_engine == "general"

    # -- reading ------------------------------------------------------------

    @api.model
    def _odin_summary_depends(self):
        return super()._odin_summary_depends() + ["per_person", "band_ids", "extra_ids"]

    def _odin_summary(self):
        if not self._odin_is_general():
            return super()._odin_summary()
        bands = len(self.band_ids)
        parts = [_("Per person") if self.per_person and self.odin_ask_people else _("Per policy"),
                 _("1 band") if bands == 1 else _("%s bands", bands)]
        if self.extra_ids:
            parts.append(_("1 extra") if len(self.extra_ids) == 1
                         else _("%s extras", len(self.extra_ids)))
        return " · ".join(parts)

    @api.model
    def _odin_rate_count_depends(self):
        return super()._odin_rate_count_depends() + ["band_ids"]

    def _odin_rate_count(self):
        if not self._odin_is_general():
            return super()._odin_rate_count()
        return len(self.band_ids)

    @api.model
    def _odin_check_depends(self):
        return super()._odin_check_depends() + [
            "band_ids.amount_min", "band_ids.amount_max", "band_ids.rate",
            "band_ids.min_premium", "band_ids.flat_premium", "extra_ids.rate",
            "extra_ids.amount", "extra_ids.name"]

    def _odin_warnings(self):
        warnings = super()._odin_warnings()
        if self._odin_is_general():
            warnings += self._odin_general_warnings()
        return warnings

    def _odin_general_warnings(self):
        warnings = []
        bands = self.band_ids
        if not bands:
            return [_("Add at least one band: a rate or a premium.")]
        for band in bands:
            if not band.rate and not band.flat_premium:
                warnings.append(_("%s has no rate and no premium.", band._odin_band_text()))
            if band.rate and not self.odin_ask_amount:
                warnings.append(_(
                    "%s has a rate, but this class asks for no amount to apply it to: "
                    "give it a premium instead.", band._odin_band_text()))
            if band.rate > HIGH_RATE:
                warnings.append(_("A rate of %(rate)s%% looks high: did you mean %(less)s%%?",
                                  rate="%g" % band.rate, less="%g" % (band.rate / 100)))
        if self.odin_ask_amount and len(bands) > 1:
            ranges = sorted((b.amount_min or 0.0, b.amount_max or 0.0) for b in bands)
            for (low, high), (next_low, _next_high) in zip(ranges, ranges[1:]):
                if not high:
                    warnings.append(_("Two bands start at %s or above and have no upper limit.",
                                      money(self.env, low)))
                elif next_low <= high:
                    warnings.append(_("Bands overlap at %s.", money(self.env, next_low)))
                elif next_low - high > 1:
                    warnings.append(_("No band between %(high)s and %(low)s.",
                                      high=money(self.env, high), low=money(self.env, next_low)))
        elif not self.odin_ask_amount and len(bands) > 1:
            warnings.append(_("This class asks for no amount, so only the first band is quoted."))
        for extra in self.extra_ids:
            if not extra.rate and not extra.amount and extra.offered == "optional":
                warnings.append(_("The extra %s has no price.", extra.name))
        same = self.search([
            ("id", "!=", self._origin.id or 0), ("line", "=", self.line),
            ("insurer_id", "=", self.insurer_id.id), ("company_id", "=", self.company_id.id)])
        if same and self.insurer_id:
            warnings.append(_("%(insurer)s already has a product in this class: %(other)s. "
                              "Both will be quoted.", insurer=self.insurer_id.name,
                              other=same[0].name))
        return warnings

    @api.model
    def _odin_try_depends(self):
        return super()._odin_try_depends() + [
            "try_amount", "try_people", "per_person", "band_ids.amount_min",
            "band_ids.amount_max", "band_ids.rate", "band_ids.min_premium",
            "band_ids.flat_premium", "extra_ids.offered", "extra_ids.rate",
            "extra_ids.min_amount", "extra_ids.amount", "extra_ids.per_person"]

    def _odin_try(self):
        """The amount and people typed in, priced by the standard screen."""
        if not self._odin_is_general():
            return super()._odin_try()
        if not self._origin.id:
            return Markup("<p class='text-muted'>%s</p>") % _(
                "Save the product with its bands to try it.")
        Quote = self.env["odin.general.quote"]
        product = self._origin
        risk = {
            "class": product._odin_class(),
            "amount": self.try_amount if self.odin_ask_amount else 0.0,
            "people": max(self.try_people, 1) if self.odin_ask_people else 1,
            "details": "",
        }
        band, reason = Quote._odin_pick_band(product, risk)
        if reason:
            return Markup("<p class='text-danger'>%s</p>") % reason
        offer = Quote._odin_general_offer(product, band, risk)
        rows = [(line.label if line.label.startswith(line.section)
                 else "%s (%s)" % (line.section, line.label), line.subtotal)
                for line in offer["core"]]
        premium = sum(line.subtotal for line in offer["core"])
        levies = self._odin_levies(offer["premium_product"], premium)
        charges = sum(line.subtotal for line in offer["charges"])
        rows += [(_("Levies"), levies), (_("Stamp duty"), charges)]
        html = self._odin_try_table(rows, _("Total"), premium + levies + charges)
        extras = [Markup("<li>%s: %s</li>") % (group["label"], self._odin_money(
            group["options"][0]["amount"])) for group in offer["groups"]]
        if extras:
            html += Markup("<p class='mt-2 mb-1'><b>%s</b></p><ul>%s</ul>") % (
                _("Extras the client can add"), Markup("").join(extras))
        return html

    def _odin_quoted(self):
        if self.odin_engine == "general":
            return bool(self.env["sale.order"].sudo().with_context(active_test=False).search_count(
                [("odin_cover_product_id", "=", self.id)], limit=1))
        return super()._odin_quoted()


class CoverBand(models.Model):
    """One band of a product: for amounts in a range, a rate on the amount
    with a minimum premium, or a fixed premium."""

    _name = "odin.cover.band"
    _description = "Rate band"
    _order = "product_id, sequence, amount_min, id"

    product_id = fields.Many2one(
        "odin.cover.product", required=True, index=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(related="product_id.company_id", store=True)
    currency_id = fields.Many2one(related="product_id.currency_id")
    label = fields.Char(
        string="Name on quotes", help="E.g. Plan A. Left empty, the band is named by its amounts.")
    amount_min = fields.Monetary(string="From", help="The lowest amount this band takes.")
    amount_max = fields.Monetary(string="Up to", help="The highest amount; empty for no limit.")
    rate = fields.Float(string="Rate (%)", digits=(16, 4),
                        help="The premium as a percentage of the amount.")
    min_premium = fields.Monetary(string="Minimum premium")
    flat_premium = fields.Monetary(
        string="Premium", help="A fixed premium, where there is no rate.")

    def _odin_band_text(self):
        """``1,000,000 to 4,999,999`` / ``Plan A`` / ``Every amount``."""
        self.ensure_one()
        low, high = self.amount_min, self.amount_max
        if low and high:
            text = _("%(low)s to %(high)s", low=money(self.env, low), high=money(self.env, high))
        elif high:
            text = _("Up to %s", money(self.env, high))
        elif low:
            text = _("%s and above", money(self.env, low))
        else:
            text = ""
        if self.label:
            return "%s (%s)" % (self.label, text) if text else self.label
        return text or _("Every amount")

    def _odin_premium(self, amount):
        """The premium for ``amount``, for one person or the policy."""
        self.ensure_one()
        if self.rate:
            return max(amount * self.rate / 100.0, self.min_premium or 0.0)
        return self.flat_premium or 0.0

    def _odin_price_text(self, amount=0.0):
        """How the premium is worked out, for the agent and the client."""
        self.ensure_one()
        if self.rate:
            text = _("%(rate)s%% of %(amount)s", rate="%g" % self.rate,
                     amount=money(self.env, amount)) if amount else _("%s%%", "%g" % self.rate)
            if self.min_premium:
                text = _("%(text)s, minimum %(min)s", text=text,
                         min=money(self.env, self.min_premium))
            return text
        return _("Fixed premium")


class CoverExtra(models.Model):
    """Something an insurer adds to a product: included, or for the client
    to add -- medical expenses, temporary disablement, an excess buy-back."""

    _name = "odin.cover.extra"
    _description = "Extra cover"
    _order = "product_id, sequence, id"

    product_id = fields.Many2one(
        "odin.cover.product", required=True, index=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    currency_id = fields.Many2one(related="product_id.currency_id")
    name = fields.Char(required=True, help="As the quote calls it, e.g. Medical expenses.")
    offered = fields.Selection(
        [("optional", "Client can add it"), ("included", "Always included")],
        required=True, default="optional")
    rate = fields.Float(string="Rate (%)", digits=(16, 4),
                        help="The price as a percentage of the amount.")
    min_amount = fields.Monetary(string="Minimum")
    amount = fields.Monetary(string="Price", help="A fixed price, where there is no rate.")
    per_person = fields.Boolean(string="Per person")

    def _odin_price(self, amount):
        """The price for ``amount``, for one person or the policy."""
        self.ensure_one()
        if self.rate:
            return max(amount * self.rate / 100.0, self.min_amount or 0.0)
        return self.amount or 0.0

    def _odin_price_text(self, amount=0.0):
        self.ensure_one()
        if self.rate:
            text = _("%(rate)s%% of %(amount)s", rate="%g" % self.rate,
                     amount=money(self.env, amount)) if amount else _("%s%%", "%g" % self.rate)
            if self.min_amount:
                text = _("%(text)s, minimum %(min)s", text=text,
                         min=money(self.env, self.min_amount))
            return text
        return _("Fixed price")
