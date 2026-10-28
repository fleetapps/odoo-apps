"""Add Motor Quote: the server side.

The same screen and the same saving as medical quotes -- contact, CRM
opportunity, one quotation per insurer, the quote -- with motor's own idea
of what is being priced: a vehicle, against each insurer's motor rate card.
Plans come back in the shape the medical screen already shows (a core
premium, optional extras with their prices), so the agent's steps are the
same whatever the insurance line.
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang, plaintext2html

from .motor_rate import COVERS

PERIODS = (1, 6, 12)


class MotorLine:
    """One priced line of a motor quote, in the shape the medical engine's
    helpers read (product, quantity, price, subtotal, who it is for)."""

    __slots__ = ("product_id", "qty", "price_unit", "label", "key", "section")

    def __init__(self, product, qty, price_unit, label, key, section):
        self.product_id = product
        self.qty = qty
        self.price_unit = price_unit
        self.label = label
        self.key = key
        self.section = section

    @property
    def subtotal(self):
        return self.qty * self.price_unit

    def who(self):
        return self.label


class MotorQuote(models.AbstractModel):
    _name = "odin.motor.quote"
    _inherit = "odin.medical.quote"
    _description = "Add Motor Quote"

    # -- the vehicle ----------------------------------------------------------

    @api.model
    def _odin_vehicle(self, cover):
        vehicle_class = self.env["odin.motor.class"].browse(
            int(cover.get("class_id") or 0)).exists()
        if not vehicle_class:
            raise UserError(_("Choose the type of vehicle."))
        kind = cover.get("cover") or "comprehensive"
        if kind not in dict(COVERS):
            raise UserError(_("Choose comprehensive or third party only."))
        value = self._odin_number(cover.get("value"), _("Vehicle value"))
        if kind == "comprehensive" and not value:
            raise UserError(_("Enter the vehicle's value: comprehensive cover is priced on it."))
        age = None
        if cover.get("year"):
            try:
                year = int(cover["year"])
            except (TypeError, ValueError):
                raise UserError(_("The year of manufacture must be a year, like 2018.")) from None
            this_year = fields.Date.context_today(self).year
            if not 1950 <= year <= this_year + 1:
                raise UserError(_("Check the year of manufacture."))
            age = max(this_year - year, 0)
        try:
            seats = int(cover.get("seats") or 0)
            tonnage = float(cover.get("tonnage") or 0)
            period = int(cover.get("period") or 12)
        except (TypeError, ValueError):
            raise UserError(_("Passengers, tonnage and period must be numbers.")) from None
        if vehicle_class.ask_seats and seats <= 0:
            raise UserError(_("Enter how many passengers the vehicle carries."))
        if vehicle_class.ask_tonnage and kind == "tpo" and tonnage <= 0:
            raise UserError(_("Enter the vehicle's tonnage."))
        if period not in PERIODS or (kind == "comprehensive" and period != 12):
            period = 12
        return {
            "class": vehicle_class,
            "cover": kind,
            "value": value if kind == "comprehensive" else 0.0,
            "age": age,
            "seats": seats,
            "tonnage": tonnage,
            "period": period,
            "registration": (cover.get("registration") or "").strip().upper(),
            "make_model": (cover.get("make_model") or "").strip(),
        }

    @api.model
    def _odin_vehicle_text(self, vehicle):
        """``KDA 123A`` or ``Private car``: the vehicle in one phrase."""
        return vehicle["registration"] or vehicle["make_model"] or vehicle["class"].name

    # -- the insurers' cards --------------------------------------------------

    @api.model
    def _odin_cards(self, vehicle):
        """Each product's line of the card for this vehicle, or why there is
        none. An insurer with two products for the vehicle is quoted on both;
        an insurer none of whose products takes the vehicle is listed once,
        with the reason."""
        rates = self.env["odin.motor.rate"].search(self._odin_rate_domain(vehicle))
        quoted, refused = [], {}
        for product in rates.product_id.sorted(lambda p: (p.insurer_id.name or "", p.sequence, p.id)):
            rate, reason = self._odin_pick_rate(
                rates.filtered(lambda r, product=product: r.product_id == product), vehicle)
            if reason:
                refused.setdefault(rate.insurer_id, (rate, reason))
            else:
                quoted.append((rate, ""))
        insurers = {rate.insurer_id for rate, _reason in quoted}
        cards = quoted + [card for insurer, card in refused.items() if insurer not in insurers]
        return sorted(cards, key=lambda card: (card[0].insurer_id.name or "", card[0].id))

    @api.model
    def _odin_pick_rate(self, rates, vehicle):
        """The band of ``rates`` (one product's) for ``vehicle``, and why
        it cannot be quoted, or ''."""
        fitting = rates.filtered(lambda r: r._odin_fits(vehicle))
        # The band the value falls in: the highest that starts at or below it.
        within = fitting.filtered(lambda r: r.value_min <= vehicle["value"])
        rate = (within.sorted("value_min")[-1:] or fitting.sorted("value_min")[:1])
        if not rate:
            periods = sorted(set(rates.mapped("period_months")))
            reason = (_("Not for this period.") if vehicle["period"] not in periods
                      else _("No rate for these details."))
            return rates[:1], reason
        return rate, rate._odin_refusal(vehicle)

    @api.model
    def _odin_rate_domain(self, vehicle):
        return [
            ("class_id", "=", vehicle["class"].id),
            ("cover", "=", vehicle["cover"]),
            ("company_id", "=", self.env.company.id),
        ]

    @api.model
    def _odin_product(self, key):
        return self.env.ref("odin_motor_quote.product_motor_%s" % key)

    @api.model
    def _odin_motor_offer(self, rate, vehicle):
        """The vehicle priced on one insurer's rate, in the medical engine's
        shape: core lines always charged, groups of optional extras, charges."""
        value, seats = vehicle["value"], vehicle["seats"]
        cover_label = dict(COVERS)[rate.cover]
        core = [MotorLine(self._odin_product("premium"), 1, rate._odin_basic(value),
                          rate._odin_rate_text(value), "basic", _("Basic premium"))]
        if rate.pll_amount and rate.pll_required and seats:
            core.append(MotorLine(
                self._odin_product("pll"), seats, rate.pll_amount,
                _("%(seats)s passengers × %(amount)s", seats=seats,
                  amount=formatLang(self.env, rate.pll_amount, digits=0)),
                "pll", _("Passenger legal liability")))
        if rate.extra_amount:
            core.append(MotorLine(self._odin_product("extra"), 1, rate.extra_amount,
                                  rate.extra_label or _("Other charge"), "extra",
                                  rate.extra_label or _("Other charge")))
        for key, label, short, mode in (
                ("pvt", _("Political violence & terrorism"), _("PVT included"), rate.pvt),
                ("excess", _("Excess protector"), _("Excess protector included"), rate.excess)):
            if mode == "included":
                core.append(MotorLine(self._odin_product(key), 1, 0.0, short, key, label))

        groups = []

        def option(key, name, line):
            return {"key": key, "label": "", "name": name, "limit": 0, "amount": line.subtotal,
                    "available": True, "reason": "", "lines": [line]}

        for key, label, mode, price, text in (
                ("pvt", _("Political violence & terrorism"), rate.pvt, rate._odin_pvt(value),
                 self._odin_extra_text(rate.pvt_rate, rate.pvt_min)),
                ("excess", _("Excess protector"), rate.excess, rate._odin_excess(value),
                 self._odin_extra_text(rate.excess_rate, rate.excess_min))):
            if mode == "optional" and price:
                line = MotorLine(self._odin_product(key), 1, price, text, key, label)
                groups.append(self._odin_group(key, label, [option(key, text, line)]))
        if rate.pll_amount and not rate.pll_required and seats:
            text = _("%(seats)s passengers × %(amount)s", seats=seats,
                     amount=formatLang(self.env, rate.pll_amount, digits=0))
            line = MotorLine(self._odin_product("pll"), seats, rate.pll_amount, text, "pll",
                             _("Passenger legal liability"))
            groups.append(self._odin_group("pll", _("Passenger legal liability"),
                                           [option("pll", text, line)]))
        courtesy = []
        for days, price in ((10, rate.courtesy_10), (20, rate.courtesy_20)):
            if price:
                text = _("%s days", days)
                courtesy.append(option("courtesy%s" % days, text, MotorLine(
                    self._odin_product("courtesy"), 1, price, text, "courtesy", _("Courtesy car"))))
        if courtesy:
            groups.append(self._odin_group("courtesy", _("Courtesy car"), courtesy))

        refusal = rate._odin_refusal(vehicle)
        stamp = self._odin_product("stamp_duty")
        return {
            "rate": rate,
            "template": self.env["sale.order.template"],
            "available": not refusal,
            "reason": refusal,
            "cover_label": cover_label,
            "core": core,
            "groups": groups,
            "charges": [MotorLine(stamp, 1, stamp.list_price, "", "statutory_charges",
                                  _("Statutory charges"))],
        }

    @api.model
    def _odin_group(self, key, label, options):
        return {"key": key, "label": label, "benefit_key": key, "requires": False,
                "basis": "policy", "options": options}

    @api.model
    def _odin_extra_text(self, rate, minimum):
        if rate:
            text = _("%s%% of value", "%g" % rate)
            if minimum:
                text = _("%(text)s, minimum %(min)s", text=text,
                         min=formatLang(self.env, minimum, digits=0))
            return text
        return _("Fixed price")

    @api.model
    def _odin_motor_plan_json(self, offer, vehicle):
        rate = offer["rate"]
        period = rate.period_months
        name = offer["cover_label"]
        if period != 12:
            name = "%s · %s" % (name, _("1 month") if period == 1 else _("%s months", period))
        return {
            "id": rate.id,
            "insurer": rate.insurer_id.name,
            "product": rate._odin_rate_text() if rate.rate else "",
            "name": name,
            "title": rate.display_name,
            "limit": vehicle["value"],
            "limit_label": _("Sum insured"),
            "type": "motor",
            "available": offer["available"],
            "reason": offer["reason"],
            "core": {
                "label": _("Basic premium"),
                "basis": "policy",
                "amount": sum(line.subtotal for line in offer["core"]),
                "parts": [self._odin_part(line) for line in offer["core"]],
            },
            "groups": [{
                "key": group["key"],
                "label": group["label"],
                "benefit_key": group["benefit_key"],
                "requires": False,
                "basis": "policy",
                "options": [{
                    "key": o["key"], "label": o["label"], "name": o["name"], "limit": 0,
                    "amount": o["amount"], "available": True, "reason": "",
                    "parts": [self._odin_part(line) for line in o["lines"]],
                } for o in group["options"]],
            } for group in offer["groups"]],
            "charges": sum(line.subtotal for line in offer["charges"]),
        }

    @api.model
    def _odin_name_products(self, plans):
        """Two products of one insurer side by side: each card says which."""
        Rate = self.env["odin.motor.rate"]
        insurers = [plan["insurer"] for plan in plans]
        for plan in plans:
            if insurers.count(plan["insurer"]) > 1:
                product = Rate.browse(plan["id"]).product_id
                plan["name"] = "%s · %s" % (plan["name"], product.quote_label or product.name)

    @api.model
    def _odin_part(self, line):
        part = super()._odin_part(line)
        if isinstance(line, MotorLine):
            part["product"] = line.section
            part["included"] = not line.price_unit
        return part

    # -- the screen's calls ---------------------------------------------------

    @api.model
    def get_setup(self, lead_id=False):
        lead = self.env["crm.lead"].browse(int(lead_id or 0)).exists()
        rates = self.env["odin.motor.rate"].search([("company_id", "=", self.env.company.id)])
        classes = []
        for vehicle_class in self.env["odin.motor.class"].search([]):
            mine = rates.filtered(lambda r: r.class_id == vehicle_class)
            if not mine:
                continue
            classes.append({
                "id": vehicle_class.id,
                "name": vehicle_class.name,
                "ask_seats": vehicle_class.ask_seats,
                "ask_tonnage": vehicle_class.ask_tonnage,
                "covers": [c for c, _label in COVERS if c in mine.mapped("cover")],
                "periods": sorted(set(mine.filtered(lambda r: r.cover == "tpo").mapped("period_months")),
                                  reverse=True),
            })
        return {
            "classes": classes,
            "insurers": sorted(set(rates.insurer_id.mapped("name"))),
            "lead": self._odin_lead_json(lead) if lead else False,
            "currency": self._odin_currency_json(),
        }

    @api.model
    def search_plans(self, params):
        """Every insurer's price for the vehicle, cheapest basic premium first."""
        vehicle = self._odin_vehicle(params.get("cover") or {})
        plans, unavailable = [], []
        for rate, reason in self._odin_cards(vehicle):
            if reason:
                unavailable.append({"id": rate.id, "title": rate.insurer_id.name, "reason": reason})
                continue
            plans.append(self._odin_motor_plan_json(self._odin_motor_offer(rate, vehicle), vehicle))
        self._odin_name_products(plans)
        plans.sort(key=lambda plan: plan["core"]["amount"])
        return {
            "vehicle": {
                "class": vehicle["class"].name,
                "cover": dict(COVERS)[vehicle["cover"]],
                "value": vehicle["value"],
                "age": vehicle["age"],
                "text": self._odin_vehicle_text(vehicle),
            },
            "plans": plans,
            "unavailable": unavailable,
        }

    @api.model
    def _odin_offer_for(self, selection, vehicle):
        rate = self.env["odin.motor.rate"].browse(self._odin_selection_key(selection)).exists()
        if not rate or rate.company_id != self.env.company:
            raise UserError(_("An insurer's rate on this quote no longer exists. Search again."))
        offer = self._odin_motor_offer(rate, vehicle)
        if not offer["available"] or not rate._odin_fits(vehicle):
            raise UserError(_("%(insurer)s cannot quote this vehicle: %(reason)s",
                              insurer=rate.insurer_id.name,
                              reason=offer["reason"] or _("its rate does not apply.")))
        return offer

    @api.model
    def _odin_motor_lines(self, offer, selection, fiscal_position=None):
        """The quotation's lines: each charge under its heading, then the
        discount, stamp duty and the policy charge."""
        fiscal_position = fiscal_position or self.env["account.fiscal.position"]
        chosen = self._odin_selected(offer, selection)
        discount = self._odin_discount(selection.get("discount"))
        policy = self._odin_number(selection.get("policy_charge"), _("Policy charge"))
        rate = offer["rate"]
        premium = list(offer["core"])
        for group in offer["groups"]:
            if group["key"] in chosen:
                premium += chosen[group["key"]]["lines"]
        lines = []

        def add(values):
            values["sequence"] = len(lines) * 10 + 10
            lines.append(values)

        def add_line(line, name):
            add(dict(self._odin_product_line(line, fiscal_position, 0), name=name))

        for line in premium:
            add(self._odin_section(line.section, line.key, 0))
            if line.key == "basic":
                name = _("%(cover)s, %(type)s: %(how)s", cover=offer["cover_label"],
                         type=rate.class_id.name, how=line.label)
            else:
                # The product already says what it is (Excess protector):
                # the description says how it is priced, or that it is included.
                name = line.label or line.section
            add_line(line, name)
        discount_lines = self._odin_discount_lines(premium, discount, fiscal_position)
        if discount_lines:
            add(self._odin_section(_("Discount"), "discount", 0))
            for values in discount_lines:
                add(values)
        add(self._odin_section(_("Statutory charges"), "statutory_charges", 0))
        for line in offer["charges"]:
            add_line(line, line.product_id.name)
        if policy:
            product = self._odin_policy_product()
            add(self._odin_section(_("Policy charge"), "policy_charge", 0))
            add({
                "product_id": product.id,
                "product_uom_qty": 1,
                "price_unit": policy,
                "tax_ids": [Command.set(self._odin_taxes(product, fiscal_position).ids)],
            })
        return lines

    @api.model
    def compute_totals(self, params):
        vehicle = self._odin_vehicle(params.get("cover") or {})
        result = {}
        for selection in params.get("plans") or []:
            key = str(self._odin_selection_key(selection))
            try:
                offer = self._odin_offer_for(selection, vehicle)
                order = self.env["sale.order"].new({
                    "company_id": self.env.company.id,
                    "order_line": [Command.create(values)
                                   for values in self._odin_motor_lines(offer, selection)],
                })
                result[key] = self._odin_totals(order, offer)
            except UserError as error:
                result[key] = {"error": error.args[0]}
        return result

    # -- saving: what motor puts in the shared save ---------------------------

    @api.model
    def _odin_quote_type(self):
        return "motor"

    @api.model
    def _odin_line_name(self):
        return _("Motor")

    @api.model
    def _odin_quote_title(self):
        return _("Motor Insurance")

    @api.model
    def _odin_people(self, context):
        return self._odin_vehicle_text(context["vehicle"])

    @api.model
    def _odin_lead_name(self, partner, context):
        return _("Motor cover – %(name)s (%(vehicle)s)",
                 name=partner.name, vehicle=self._odin_people(context))

    @api.model
    def _odin_selection_key(self, selection):
        return int(selection.get("rate_id") or 0)

    @api.model
    def _odin_order_key(self, order):
        return order.odin_motor_rate_id.id

    @api.model
    def _odin_price(self, params):
        vehicle = self._odin_vehicle(params.get("cover") or {})
        days = self.env.company.quotation_validity_days or 30
        priced = []
        for selection in params.get("plans") or []:
            offer = self._odin_offer_for(selection, vehicle)
            self._odin_selected(offer, selection)
            rate = offer["rate"]
            priced.append({
                "key": rate.id,
                "recommended": bool(selection.get("recommended")),
                "days": days,
                "order_values": {
                    "odin_motor_rate_id": rate.id,
                    "note": plaintext2html(rate.note) if rate.note else False,
                },
                "lines": lambda fiscal_position, offer=offer, selection=selection:
                    self._odin_motor_lines(offer, selection, fiscal_position),
            })
        return {"vehicle": vehicle}, priced

    @api.model
    def _odin_comparison_values(self, params, context, orders):
        return {
            "member_ids": [Command.clear()],
            "detail_ids": [Command.clear()],
            "template_ids": [Command.clear()],
        }

    @api.model
    def _odin_current_params(self, params):
        return params or False

    @api.model
    def _odin_prefill_params(self, params):
        """The same vehicle a year on: the value is the agent's to update."""
        params = dict(params)
        params.pop("customer", None)
        return params
