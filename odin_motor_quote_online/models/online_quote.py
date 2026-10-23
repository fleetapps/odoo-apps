"""The online motor quote page's server side.

What the visitor drives, full cover or third party only, its value and year
(and passengers or tonnage where they count), then every insurer's price for
it with the extras each offers in plain words, then where to send the quote.
Priced by Add Motor Quote's engine; saved, assigned and followed up exactly
as an online medical quote is (odin_medical_quote_online), which this
inherits.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import LazyTranslate

from odoo.addons.odin_motor_quote.models.motor_rate import COVERS

_lt = LazyTranslate(__name__)

#: The extras an insurer may sell with the cover, in plain words.
EXTRAS = {
    "pvt": (_lt("Riots, strikes & terrorism"),
            _lt("Damage from riots, strikes, political violence or terrorism (PVT)")),
    "excess": (_lt("Excess protector"),
               _lt("Pays the excess: the part of each claim you would otherwise pay yourself")),
    "pll": (_lt("Passenger cover"),
            _lt("Injuries to your passengers (passenger legal liability)")),
    "courtesy": (_lt("Replacement car"),
                 _lt("A car to use while yours is repaired after an accident")),
}
COVER_WORDS = {
    "comprehensive": (_lt("Full cover (comprehensive)"),
                      _lt("Your own vehicle too: accident damage, theft and fire, as well as "
                          "damage and injuries you cause to others.")),
    "tpo": (_lt("Third party only"),
            _lt("The legal minimum: damage and injuries you cause to others. Not your own "
                "vehicle.")),
}
OLDEST = 40


class MotorQuoteOnline(models.AbstractModel):
    _name = "odin.motor.quote.online"
    _inherit = "odin.medical.quote.online"
    _description = "Online motor quote"

    # -- per line -------------------------------------------------------------

    @api.model
    def _odin_type(self):
        return "motor"

    @api.model
    def _odin_path(self):
        return "/motor-quote"

    @api.model
    def _odin_source_name(self):
        return "Online motor quote"

    @api.model
    def _odin_page_template(self):
        return "odin_motor_quote_online.page"

    @api.model
    def _quoter(self):
        """Add Motor Quote's engine, limited to what is shown online."""
        return self.env["odin.motor.quote"].sudo().with_context(odin_online=True)

    @api.model
    def _vehicle_text(self, cover):
        vehicle_class = self.env["odin.motor.class"].sudo().browse(
            int(cover.get("class_id") or 0)).exists()
        return (cover.get("registration") or cover.get("make_model")
                or vehicle_class.online_name or vehicle_class.name or _("vehicle"))

    @api.model
    def _lead_name(self, partner, cover):
        return _("Online motor quote – %(name)s (%(vehicle)s)",
                 name=partner.name, vehicle=self._vehicle_text(cover))

    @api.model
    def _note_lines(self, cover):
        return [_("Vehicle value, year and use as the client gave them: confirm them (logbook, "
                  "valuation) before the quote is final.")]

    @api.model
    def _share_message(self, hello, link):
        return _("%(hello)s you can compare motor insurance prices for your vehicle and get "
                 "your quote here, in about two minutes:\n%(link)s", hello=hello, link=link)

    # -- the vehicle ------------------------------------------------------------

    @api.model
    def _classes(self):
        """The vehicle types the public can price: shown online and with a
        rate shown online."""
        rates = self.env["odin.motor.rate"].sudo().search([
            ("company_id", "=", self.env.company.id), ("odin_online", "=", True),
            ("class_id.odin_online", "=", True)])
        result = []
        for vehicle_class in rates.class_id.sorted(lambda c: (c.sequence, c.id)):
            mine = rates.filtered(lambda r, c=vehicle_class: r.class_id == c)
            covers = [c for c, _label in COVERS if c in mine.mapped("cover")]
            result.append({
                "id": vehicle_class.id,
                "name": vehicle_class.online_name or vehicle_class.name,
                "hint": vehicle_class.online_hint or "",
                "icon": vehicle_class.online_icon or "fa-car",
                "ask_seats": vehicle_class.ask_seats,
                "ask_tonnage": vehicle_class.ask_tonnage,
                "covers": [{"key": c, "label": str(COVER_WORDS[c][0]),
                            "hint": str(COVER_WORDS[c][1])} for c in covers],
                "periods": sorted(set(mine.filtered(lambda r: r.cover == "tpo").mapped(
                    "period_months")), reverse=True),
            })
        return result

    @api.model
    def _cover(self, vehicle):
        """The cover details Add Motor Quote works from, out of the answers
        on the page, checked against what is offered online."""
        vehicle = vehicle or {}
        classes = {c["id"]: c for c in self._classes()}
        try:
            class_id = int(vehicle.get("class_id") or 0)
        except (TypeError, ValueError):
            class_id = 0
        offered = classes.get(class_id)
        if not offered:
            raise UserError(_("Tell us what you drive."))
        kind = vehicle.get("cover")
        if kind not in [c["key"] for c in offered["covers"]]:
            raise UserError(_("Choose full cover or third party only."))
        this_year = fields.Date.context_today(self).year
        try:
            year = int(vehicle.get("year") or 0)
        except (TypeError, ValueError):
            year = 0
        if not this_year - OLDEST <= year <= this_year + 1:
            raise UserError(_("Choose the year it was made."))
        cover = {"class_id": class_id, "cover": kind, "year": year, "period": 12,
                 "registration": " ".join(str(vehicle.get("registration") or "").split())[:20].upper(),
                 "make_model": " ".join(str(vehicle.get("make_model") or "").split())[:60]}
        if kind == "comprehensive":
            try:
                value = float(vehicle.get("value") or 0)
            except (TypeError, ValueError):
                value = 0
            if not 10000 <= value <= 1000000000:
                raise UserError(_("Enter what the vehicle is worth today."))
            cover["value"] = value
        else:
            periods = offered["periods"] or [12]
            try:
                period = int(vehicle.get("period") or periods[0])
            except (TypeError, ValueError):
                period = periods[0]
            cover["period"] = period if period in periods else periods[0]
        if offered["ask_seats"]:
            try:
                seats = int(vehicle.get("seats") or 0)
            except (TypeError, ValueError):
                seats = 0
            if not 1 <= seats <= 100:
                raise UserError(_("Enter how many passengers it can carry."))
            cover["seats"] = seats
        if offered["ask_tonnage"] and kind == "tpo":
            try:
                tonnage = float(vehicle.get("tonnage") or 0)
            except (TypeError, ValueError):
                tonnage = 0
            if not 0 < tonnage <= 100:
                raise UserError(_("Enter how many tonnes it can carry."))
            cover["tonnage"] = tonnage
        return cover

    # -- prices -----------------------------------------------------------------


    @api.model
    def _included(self, offer, vehicle):
        """What comes with the price, beyond the cover itself, in plain words."""
        rows = []
        for line in offer["core"][1:]:
            if line.key in ("pvt", "excess"):
                rows.append(str(EXTRAS[line.key][0]))
            elif line.key == "pll":
                rows.append(_("Passenger cover for %s passengers", vehicle["seats"]))
            else:
                rows.append(line.label or line.section)
        return rows

    @api.model
    def _extras(self, offer, picked):
        """The extras the visitor can add, each with what it costs."""
        result = []
        for group in offer["groups"]:
            if group["key"] not in EXTRAS:
                continue
            options = [{"key": o["key"],
                        "label": o["name"] if group["key"] == "courtesy" else "",
                        "amount": o["amount"]} for o in group["options"]]
            chosen = next((o["key"] for o in options if o["key"] in picked), "")
            result.append({"key": group["key"], "label": str(EXTRAS[group["key"]][0]),
                           "help": str(EXTRAS[group["key"]][1]), "options": options,
                           "chosen": chosen})
        return result

    @api.model
    def _plan_name(self, offer, vehicle):
        name = str(COVER_WORDS[offer["rate"].cover][0])
        period = offer["rate"].period_months
        if period != 12:
            name = "%s · %s" % (name, _("1 month") if period == 1 else _("%s months", period))
        return name

    @api.model
    def options(self, vehicle, extras=None, only=None):
        """Every insurer's price for the vehicle with the extras picked,
        cheapest first, levies and stamp duty included. With none, why."""
        cover = self._cover(vehicle)
        extras = extras or {}
        only = {int(i) for i in only or []}
        quoter = self._quoter()
        car = quoter._odin_vehicle(cover)
        offers, refused, selections = [], [], []
        for rate, reason in quoter._odin_cards(car):
            if reason:
                refused.append(reason)
                continue
            if only and rate.id not in only:
                continue
            offer = quoter._odin_motor_offer(rate, car)
            wanted = {str(key) for key in extras.get(str(rate.id)) or []}
            options = self._extras(offer, wanted)
            offers.append((offer, options))
            selections.append({"rate_id": rate.id,
                               "options": [extra["chosen"] for extra in options if extra["chosen"]]})
        totals = quoter.compute_totals({"cover": cover, "plans": selections}) if selections else {}
        plans = []
        for offer, options in offers:
            rate = offer["rate"]
            plan_totals = totals.get(str(rate.id)) or {}
            if not plan_totals or "error" in plan_totals:
                continue
            monthly = sorted(plan_totals.get("instalments") or [], key=lambda i: i["months"])
            plans.append({
                "id": rate.id,
                "insurer": rate.insurer_id.name,
                "name": self._plan_name(offer, car),
                "per": _("a year") if rate.period_months == 12 else (
                    _("for 1 month") if rate.period_months == 1
                    else _("for %s months", rate.period_months)),
                "cover": rate.cover,
                "value": car["value"],
                "basic": offer["core"][0].subtotal,
                "included": self._included(offer, car),
                "extras": options,
                "premium": plan_totals["premium"],
                "levies": plan_totals["levies"],
                "total": plan_totals["total"],
                "monthly": {"months": monthly[-1]["months"],
                            "instalment": monthly[-1]["instalment"]}
                if monthly and rate.period_months == 12 else False,
            })
        plans.sort(key=lambda p: (p["total"], p["id"]))
        return {
            "plans": plans,
            # The most common reason, when nobody can quote it online.
            "refused": max(set(refused), key=refused.count) if refused and not plans else "",
        }

    # -- the quote --------------------------------------------------------------

    @api.model
    def submit(self, vehicle, extras=None, chosen=None, contact=None, tokens=None):
        """Save the visitor's quote and return the link to their page."""
        contact = self._check_contact(contact or {})
        chosen = self._check_chosen(chosen)
        self._check_caps(contact["digits"])
        cover = self._cover(vehicle)
        # Priced again here: nothing the browser says about a price is used.
        priced = {plan["id"]: plan for plan in self.options(vehicle, extras, only=chosen)["plans"]}
        if any(rate_id not in priced for rate_id in chosen):
            raise UserError(_("An insurer you chose can no longer quote this. Please choose again."))
        return self._save(cover, [{
            "rate_id": rate_id,
            "options": [extra["chosen"] for extra in priced[rate_id]["extras"] if extra["chosen"]],
        } for rate_id in chosen], contact, tokens or {})

    # -- the client's page ------------------------------------------------------

    @api.model
    def _page_setup(self):
        this_year = fields.Date.context_today(self).year
        return {
            "classes": self._classes(),
            "years": list(range(this_year + 1, this_year - OLDEST - 1, -1)),
            "extras": [{"key": key, "label": str(label), "help": str(help_text)}
                       for key, (label, help_text) in EXTRAS.items()],
        }

    @api.model
    def _prefill_hints(self, hints):
        """The vehicle type, cover and period a link may carry, if offered."""
        try:
            class_id = int(hints.get("class_id") or 0)
            period = int(hints.get("period") or 0)
        except (TypeError, ValueError):
            return {}
        offered = next((c for c in self._classes() if c["id"] == class_id), None)
        if not offered:
            return {}
        vehicle = {"class_id": class_id}
        covers = [c["key"] for c in offered["covers"]]
        if hints.get("cover") in covers:
            vehicle["cover"] = hints["cover"]
        elif len(covers) == 1:
            vehicle["cover"] = covers[0]
        if vehicle.get("cover") == "tpo" and period in offered["periods"]:
            vehicle["period"] = period
        return {"vehicle": vehicle}

    @api.model
    def _prefill(self, previous):
        cover = (previous.odin_builder_params or {}).get("cover") or {}
        plans = (previous.odin_builder_params or {}).get("plans") or []
        return {
            "vehicle": {key: cover.get(key) or "" for key in (
                "class_id", "cover", "value", "year", "seats", "tonnage", "period")},
            "registration": cover.get("registration") or "",
            "make_model": cover.get("make_model") or "",
            "extras": {str(p.get("rate_id")): p.get("options") or [] for p in plans},
        }

    @api.model
    def _plan_heading(self, order, plan):
        rate = order.odin_motor_rate_id
        if not rate:
            return super()._plan_heading(order, plan)
        name = str(COVER_WORDS[rate.cover][0])
        if rate.period_months != 12:
            name = "%s · %s" % (name, _("1 month") if rate.period_months == 1
                                else _("%s months", rate.period_months))
        return rate.insurer_id.name, name

    @api.model
    def _plain_sections(self, order, comparison):
        cover = (comparison.odin_builder_params or {}).get("cover") or {}
        rows = []
        for section in order._client_sections():
            key = section["key"]
            if not section["included"] or key in (
                    "statutory_charges", "payment_options", "discount", "policy_charge"):
                continue
            if key == "basic":
                if order.odin_motor_rate_id.cover == "comprehensive":
                    value = self._whole_money(float(cover.get("value") or 0), order.currency_id)
                    rows.append({"label": _("Your vehicle"), "text": _("insured for %s", value)})
                else:
                    rows.append({"label": _("Others"), "text": _(
                        "damage and injuries you cause to other people")})
                continue
            free = not section["amount"]
            label = str(EXTRAS[key][0]) if key in EXTRAS else section["label"]
            text = _("included") if free else ""
            if key == "courtesy":
                text = ", ".join(entry["name"] for entry in section["included"] if entry.get("name"))
            rows.append({"label": label, "text": text})
        return rows

    @api.model
    def _quote_texts(self, comparison):
        cover = (comparison.odin_builder_params or {}).get("cover") or {}
        period = int(cover.get("period") or 12)
        vehicle = " · ".join(p for p in (cover.get("registration"), cover.get("make_model")) if p)
        return {
            "kind": _("motor"),
            "about": vehicle or self._vehicle_text(cover),
            "per": _("a year") if period == 12 else (
                _("for 1 month") if period == 1 else _("for %s months", period)),
            "monthly": period == 12,
            "fineprint": _(
                "Prices include government levies and stamp duty. The insurer may ask to "
                "inspect or value the vehicle before cover starts; your advisor confirms the "
                "cover with the insurer before you pay, and the policy document sets out "
                "exactly what is covered."),
        }
