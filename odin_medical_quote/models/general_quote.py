"""The standard quote screen: any class the office adds, quoted like motor.

The same screen and the same saving as medical and motor quotes -- the
client, the CRM opportunity, one quotation per insurer, the quote that
compares them -- with the class's own questions: the amount insured, the
people covered and a line of details, as the class asks for them. Every
product of the class is priced on its bands and extras (cover_general.py)
and comes back in the shape the medical screen shows, so picking,
comparing, discounting, saving, accepting and renewing work unchanged.
"""

from odoo import Command, _, api, models
from odoo.exceptions import UserError
from odoo.tools import formatLang, plaintext2html

#: the most people one quote prices
MAX_PEOPLE = 10000


class CoverLine:
    """One priced line of a standard quote, in the shape the medical
    engine's helpers read (product, quantity, price, subtotal, who)."""

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


class GeneralQuote(models.AbstractModel):
    _name = "odin.general.quote"
    _inherit = "odin.medical.quote"
    _description = "Add Quote (standard screen)"

    # -- the class and what was entered ----------------------------------------

    @api.model
    def _odin_class(self, class_id=None):
        """The class being quoted: the one asked for, or the one this call
        was made for (saving)."""
        record = self.env["odin.insurance.class"].browse(
            int(class_id or self.env.context.get("odin_class_id") or 0)).exists()
        if not record or record.engine != "general":
            raise UserError(_("Choose what kind of insurance to quote."))
        return record

    @api.model
    def _odin_risk(self, cover):
        """What is being insured, from the cover details step."""
        klass = self._odin_class(cover.get("class_id"))
        amount = 0.0
        if klass.ask_amount:
            label = klass.amount_label or _("Sum insured")
            amount = self._odin_number(cover.get("amount"), label)
            if not amount:
                raise UserError(_("Enter the %s.", label.lower()))
        people = 1
        if klass.ask_people:
            try:
                people = int(cover.get("people") or 0)
            except (TypeError, ValueError):
                raise UserError(_("The number of people must be a whole number.")) from None
            if not 1 <= people <= MAX_PEOPLE:
                raise UserError(_("Enter how many people to cover, from 1 to %s.", MAX_PEOPLE))
        return {
            "class": klass,
            "amount": amount,
            "people": people,
            "details": (cover.get("details") or "").strip(),
        }

    @api.model
    def _odin_risk_text(self, risk):
        """``Teacher`` / ``12 people`` / ``Sum insured 1,000,000``: what is
        insured, in one phrase."""
        if risk["details"]:
            return risk["details"]
        klass = risk["class"]
        if klass.ask_people:
            return _("1 person") if risk["people"] == 1 else _("%s people", risk["people"])
        if klass.ask_amount:
            return "%s %s" % (klass.amount_label or _("Sum insured"),
                              formatLang(self.env, risk["amount"], digits=0))
        return klass.name

    # -- the insurers' products -------------------------------------------------

    @api.model
    def _odin_products(self, risk):
        return self.env["odin.cover.product"].search([
            ("line", "=", risk["class"].code),
            ("company_id", "=", self.env.company.id),
        ]).sorted(lambda p: (p.insurer_id.name or "", p.sequence, p.id))

    @api.model
    def _odin_pick_band(self, product, risk):
        """The band of ``product`` for ``risk``, and why it cannot be
        quoted, or ''."""
        bands = product.band_ids.sorted(lambda b: (b.sequence, b.amount_min, b.id))
        if not bands:
            return bands, _("No rates yet.")
        if not risk["class"].ask_amount:
            return bands[:1], ""
        amount = risk["amount"]
        fitting = bands.filtered(lambda b: (not b.amount_min or amount >= b.amount_min)
                                 and (not b.amount_max or amount <= b.amount_max))
        if fitting:
            # The band the amount falls in: the narrowest, starting nearest.
            return fitting.sorted(lambda b: (b.amount_min, b.id))[-1:], ""
        lowest = min(bands.mapped("amount_min"))
        if amount < lowest:
            return bands[:1], _("Amounts from %s.", formatLang(self.env, lowest, digits=0))
        if all(bands.mapped("amount_max")):
            return bands[:1], _("Amounts up to %s.", formatLang(
                self.env, max(bands.mapped("amount_max")), digits=0))
        return bands[:1], _("No rate for %s.", formatLang(self.env, amount, digits=0))

    @api.model
    def _odin_stamp_product(self):
        return self.env.ref("odin_medical_quote.product_general_stamp_duty")

    @api.model
    def _odin_general_offer(self, product, band, risk):
        """``risk`` priced on one product, in the medical engine's shape:
        core lines always charged, groups of optional extras, charges."""
        amount, klass = risk["amount"], risk["class"]
        people = risk["people"] if klass.ask_people else 1
        premium_product = klass._odin_premium_product()

        def times(per_person):
            return people if per_person and klass.ask_people else 1

        def label(how, qty):
            return how if qty == 1 else self.env._("%s, per person", how)

        qty = times(product.per_person)
        how = band._odin_price_text(amount)
        if band.label:
            how = "%s: %s" % (band.label, how)
        core = [CoverLine(premium_product, qty, band._odin_premium(amount), label(how, qty),
                          "basic", _("Basic premium"))]
        groups = []
        for extra in product.extra_ids:
            qty = times(extra.per_person)
            price = extra._odin_price(amount)
            text = label(extra._odin_price_text(amount), qty)
            key = "extra_%s" % extra.id
            if extra.offered == "included":
                core.append(CoverLine(premium_product, qty if price else 1, price,
                                      text if price else _("%s included", extra.name),
                                      key, extra.name))
            elif price:
                line = CoverLine(premium_product, qty, price, text, key, extra.name)
                groups.append({
                    "key": key, "label": extra.name, "benefit_key": key, "requires": False,
                    "basis": "person" if qty > 1 else "policy",
                    "options": [{"key": key, "label": "", "name": text, "limit": 0,
                                 "amount": line.subtotal, "available": True, "reason": "",
                                 "lines": [line]}],
                })
        stamp = self._odin_stamp_product()
        return {
            "product": product,
            "band": band,
            "template": self.env["sale.order.template"],
            "premium_product": premium_product,
            "available": True,
            "reason": "",
            "core": core,
            "groups": groups,
            "charges": [CoverLine(stamp, 1, stamp.list_price, "", "statutory_charges",
                                  _("Statutory charges"))],
        }

    @api.model
    def _odin_general_plan_json(self, offer, risk):
        product, band = offer["product"], offer["band"]
        klass = risk["class"]
        name = product.quote_label or product.name
        if band.label:
            name = "%s · %s" % (name, band.label)
        return {
            "id": product.id,
            "insurer": product.insurer_id.name,
            "product": "",
            "name": name,
            "title": "%s – %s" % (product.insurer_id.name, product.quote_label or product.name),
            "limit": risk["amount"] if klass.ask_amount else 0,
            "limit_label": klass.amount_label or _("Sum insured"),
            "type": klass.code,
            "available": True,
            "reason": "",
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
    def _odin_part(self, line):
        part = super()._odin_part(line)
        if isinstance(line, CoverLine):
            part["product"] = line.section
            part["included"] = not line.price_unit
            if line.qty > 1:
                # "Basic premium: 3 × 4,000" -- how it is worked out is in the name.
                part["who"] = line.section
        return part

    # -- the screen's calls -----------------------------------------------------

    @api.model
    def get_setup(self, lead_id=False, class_id=False):
        klass = self._odin_class(class_id)
        lead = self.env["crm.lead"].browse(int(lead_id or 0)).exists()
        products = self._odin_products({"class": klass})
        return {
            "class": {
                "id": klass.id,
                "code": klass.code,
                "name": klass.name,
                "icon": klass.icon,
                "ask_amount": klass.ask_amount,
                "amount_label": klass.amount_label or _("Sum insured"),
                "ask_people": klass.ask_people,
                "people_label": klass.people_label or _("People covered"),
                "details_label": klass.details_label or _("What is insured"),
            },
            "products": len(products),
            "insurers": sorted(set(products.insurer_id.mapped("name"))),
            "lead": self._odin_lead_json(lead) if lead else False,
            "currency": self._odin_currency_json(),
        }

    @api.model
    def search_plans(self, params):
        """Every insurer's price for what is insured, cheapest first."""
        risk = self._odin_risk(params.get("cover") or {})
        plans, unavailable = [], []
        for product in self._odin_products(risk):
            band, reason = self._odin_pick_band(product, risk)
            if reason:
                unavailable.append({"id": product.id, "reason": reason,
                                    "title": "%s – %s" % (product.insurer_id.name, product.name)})
                continue
            plans.append(self._odin_general_plan_json(
                self._odin_general_offer(product, band, risk), risk))
        plans.sort(key=lambda plan: plan["core"]["amount"])
        klass = risk["class"]
        return {
            "risk": {
                "class": klass.name,
                "amount": risk["amount"] if klass.ask_amount else None,
                "amount_label": klass.amount_label or _("Sum insured"),
                "people": risk["people"] if klass.ask_people else None,
                "details": risk["details"],
            },
            "plans": plans,
            "unavailable": unavailable,
        }

    @api.model
    def _odin_offer_for(self, selection, risk):
        product = self.env["odin.cover.product"].browse(
            self._odin_selection_key(selection)).exists()
        if not product or product.line != risk["class"].code \
                or product.company_id != self.env.company:
            raise UserError(_("An insurer's product on this quote no longer exists. Search again."))
        band, reason = self._odin_pick_band(product, risk)
        if reason:
            raise UserError(_("%(insurer)s cannot quote this: %(reason)s",
                              insurer=product.insurer_id.name, reason=reason))
        return self._odin_general_offer(product, band, risk)

    @api.model
    def _odin_general_lines(self, offer, selection, fiscal_position=None):
        """The quotation's lines: the premium and each extra under its
        heading, what the product pays out under the premium, then the
        discount, stamp duty and the policy charge."""
        fiscal_position = fiscal_position or self.env["account.fiscal.position"]
        chosen = self._odin_selected(offer, selection)
        discount = self._odin_discount(selection.get("discount"))
        policy = self._odin_number(selection.get("policy_charge"), _("Policy charge"))
        product = offer["product"]
        premium = list(offer["core"])
        for group in offer["groups"]:
            if group["key"] in chosen:
                premium += chosen[group["key"]]["lines"]
        lines = []

        def add(values):
            values["sequence"] = len(lines) * 10 + 10
            lines.append(values)

        for line in premium:
            add(self._odin_section(line.section, line.key, 0))
            name = line.label or line.section
            if line.key == "basic":
                name = "%s: %s" % (product.quote_label or product.name, line.label)
            elif line.label != line.section and line.price_unit:
                name = "%s: %s" % (line.section, line.label)
            add(dict(self._odin_product_line(line, fiscal_position, 0), name=name))
            if line.key == "basic" and (product.benefits or "").strip():
                add({"display_type": "line_note", "name": self._odin_benefits_note(product),
                     "product_uom_qty": 0})
        discount_lines = self._odin_discount_lines(premium, discount, fiscal_position)
        if discount_lines:
            add(self._odin_section(_("Discount"), "discount", 0))
            for values in discount_lines:
                add(values)
        add(self._odin_section(_("Statutory charges"), "statutory_charges", 0))
        for line in offer["charges"]:
            add(dict(self._odin_product_line(line, fiscal_position, 0), name=line.product_id.name))
        if policy:
            policy_product = self._odin_policy_product()
            add(self._odin_section(_("Policy charge"), "policy_charge", 0))
            add({
                "product_id": policy_product.id,
                "product_uom_qty": 1,
                "price_unit": policy,
                "tax_ids": [Command.set(self._odin_taxes(policy_product, fiscal_position).ids)],
            })
        return lines

    @api.model
    def _odin_benefits_note(self, product):
        """What the product pays out, as a titled list for What's covered."""
        items = []
        for row in (product.benefits or "").splitlines():
            row = row.strip()
            if row:
                items.append(row if row.startswith("•") else "• %s" % row.lstrip("-* "))
        return "\n".join([_("%s: what's covered", product.quote_label or product.name)] + items)

    @api.model
    def compute_totals(self, params):
        risk = self._odin_risk(params.get("cover") or {})
        result = {}
        for selection in params.get("plans") or []:
            key = str(self._odin_selection_key(selection))
            try:
                offer = self._odin_offer_for(selection, risk)
                order = self.env["sale.order"].new({
                    "company_id": self.env.company.id,
                    "order_line": [Command.create(values)
                                   for values in self._odin_general_lines(offer, selection)],
                })
                result[key] = self._odin_totals(order, offer)
            except UserError as error:
                result[key] = {"error": error.args[0]}
        return result

    # -- saving: what the class puts in the shared save ------------------------

    @api.model
    def save_quote(self, params):
        klass = self._odin_class((params.get("cover") or {}).get("class_id"))
        return super(GeneralQuote, self.with_context(odin_class_id=klass.id)).save_quote(params)

    @api.model
    def _odin_quote_type(self):
        return self._odin_class().code

    @api.model
    def _odin_line_name(self):
        return self._odin_class().name

    @api.model
    def _odin_quote_title(self):
        return _("%s Insurance", self._odin_class().name)

    @api.model
    def _odin_people(self, context):
        return self._odin_risk_text(context["risk"])

    @api.model
    def _odin_lead_name(self, partner, context):
        return _("%(cover)s cover – %(name)s (%(what)s)", cover=context["risk"]["class"].name,
                 name=partner.name, what=self._odin_people(context))

    @api.model
    def _odin_selection_key(self, selection):
        return int(selection.get("product_id") or 0)

    @api.model
    def _odin_order_key(self, order):
        return order.odin_cover_product_id.id

    @api.model
    def _odin_price(self, params):
        risk = self._odin_risk(params.get("cover") or {})
        days = self.env.company.quotation_validity_days or 30
        priced = []
        for selection in params.get("plans") or []:
            offer = self._odin_offer_for(selection, risk)
            self._odin_selected(offer, selection)
            product = offer["product"]
            priced.append({
                "key": product.id,
                "recommended": bool(selection.get("recommended")),
                "days": days,
                "order_values": {
                    "odin_cover_product_id": product.id,
                    "note": plaintext2html(product.note) if product.note else False,
                },
                "lines": lambda fiscal_position, offer=offer, selection=selection:
                    self._odin_general_lines(offer, selection, fiscal_position),
            })
        return {"risk": risk}, priced

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
        """The same cover a year on: the amount is the agent's to update."""
        params = dict(params)
        params.pop("customer", None)
        return params
