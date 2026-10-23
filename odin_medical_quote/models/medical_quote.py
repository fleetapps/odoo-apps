"""Add Medical Quote: what the screen asks the server.

The screen holds nothing it could get wrong. It sends what the agent typed --
who is covered, the limit range, the plans and options ticked, a discount --
and this prices it from the rate cards every time, both for the live totals
and when the quote is saved. So the figure the agent reads out on the call
is the figure on the quotation, because the same code produced both.

Saving does what the agent would otherwise do in four apps: the client goes
into Contacts, the opportunity into the CRM pipeline, one quotation per plan
is created against that opportunity, and the cover comparison that prints
them side by side is made from those quotations.
"""

import copy
import re

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .pricing import RateBook
from .rating import Household

#: a quotation that has gone no further than being sent can still be redone
EDITABLE_ORDER_STATES = ("draft", "sent")
#: what marks a global discount line for Odoo (see sale.order.line)
DISCOUNT_KEY = "global_discount,odin_medical_quote"
#: days until the follow-up a new quote puts on its opportunity
FOLLOW_UP_DAYS = 2


class MedicalQuote(models.AbstractModel):
    _name = "odin.medical.quote"
    _description = "Add Medical Quote"

    # -- what the agent entered -------------------------------------------

    @api.model
    def _odin_age(self, value, who):
        """Age in whole years today from a date of birth, or None if blank."""
        if not value:
            return None
        born = fields.Date.to_date(value)
        today = fields.Date.context_today(self)
        if born > today:
            raise UserError(_("The %s's date of birth is in the future.", who))
        age = relativedelta(today, born).years
        if age > 120:
            raise UserError(_("Check the %s's date of birth: that is %s years ago.", who, age))
        return age

    @api.model
    def _odin_household(self, cover):
        """Who is being covered, from the cover details step."""
        children = cover.get("children") or 0
        try:
            children = int(children)
        except (TypeError, ValueError):
            raise UserError(_("Number of children must be a whole number.")) from None
        if not 0 <= children <= 20:
            raise UserError(_("Number of children must be between 0 and 20."))
        if cover.get("children_only"):
            if not children:
                raise UserError(_("Enter how many children to cover."))
            return Household(children=children)
        principal = self._odin_age(cover.get("principal_dob"), _("principal"))
        if principal is None:
            raise UserError(_("Enter the principal's date of birth."))
        spouse = None
        if cover.get("include_spouse"):
            spouse = self._odin_age(cover.get("spouse_dob"), _("spouse"))
            if spouse is None:
                raise UserError(_(
                    "Enter the spouse's date of birth, or untick Include spouse."))
        return Household(principal, spouse, children)

    @api.model
    def _odin_number(self, value, what):
        """A typed amount: '' is nothing, '1,500' is 1500."""
        text = str(value if value is not None else "").replace(",", "").strip()
        if not text:
            return 0.0
        try:
            number = float(text)
        except ValueError:
            raise UserError(_("%s must be a number.", what)) from None
        if number < 0:
            raise UserError(_("%s cannot be negative.", what))
        return number

    @api.model
    def _odin_discount(self, value):
        """``'10%'`` -> ('percent', 10); ``'2,500'`` -> ('fixed', 2500)."""
        text = str(value or "").replace(",", "").replace(" ", "")
        if not text:
            return None
        percent = text.endswith("%")
        try:
            amount = float(text.rstrip("%"))
        except ValueError:
            raise UserError(_(
                "Enter the discount as a percentage, like 10%, or an amount, like 2500."
            )) from None
        if amount < 0:
            raise UserError(_("A discount cannot be negative."))
        if not amount:
            return None
        if percent and amount > 100:
            raise UserError(_("A discount cannot be more than 100%."))
        return ("percent" if percent else "fixed", amount)

    @api.model
    def _odin_templates(self, cover):
        """The plans the cover details ask for, by insurer and then limit."""
        domain = [
            ("odin_medical", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ]
        if cover.get("template_id"):
            domain.append(("id", "=", int(cover["template_id"])))
        # A family plan is not "unavailable" to a children-only quote, it is
        # simply not what was asked for: left out rather than listed.
        domain.append(("odin_plan_type", "=",
                       "child_only" if cover.get("children_only") else "family"))
        templates = self.env["sale.order.template"].search(domain)
        insurer = cover.get("insurer")
        if insurer:
            templates = templates.filtered(lambda t: t._odin_insurer_name() == insurer)
        if not cover.get("template_id"):
            low = self._odin_number(cover.get("limit_min"), _("Minimum limit"))
            high = self._odin_number(cover.get("limit_max"), _("Maximum limit"))
            if high and low > high:
                raise UserError(_("The minimum limit is above the maximum limit."))
            templates = templates.filtered(
                lambda t: t.odin_inpatient_limit >= low
                and (not high or t.odin_inpatient_limit <= high))
        return templates.sorted(lambda t: (
            t._odin_insurer_name(), t.odin_inpatient_limit, t.sequence, t.id))

    # -- what the screen shows --------------------------------------------

    @api.model
    def _odin_part(self, line):
        return {
            "who": line.who(),
            "qty": line.qty,
            "price": line.price_unit,
            "amount": line.subtotal,
            "product": line.product_id.with_context(display_default_code=False).display_name,
        }

    @api.model
    def _odin_plan_json(self, offer):
        template = offer["template"]
        product, plan = template._odin_heading()
        core_block = next((b for b in offer["blocks"] if b["kind"] == "core"), None)
        core = offer["core"]
        return {
            "id": template.id,
            "insurer": template._odin_insurer_name(),
            "product": product,
            "name": plan or template.name,
            "title": template.name,
            "limit": template.odin_inpatient_limit,
            "type": template.odin_plan_type,
            "available": offer["available"],
            "reason": offer["reason"],
            "core": {
                "label": core_block["label"] if core_block else _("Inpatient"),
                "basis": core[0].product_id.odin_rate_basis if core else "policy",
                "amount": sum(line.subtotal for line in core),
                "parts": [self._odin_part(line) for line in core],
            },
            "groups": [{
                "key": group["key"],
                "label": group["label"],
                "benefit_key": group["benefit_key"],
                "requires": group["requires"],
                "basis": group["basis"],
                "options": [{
                    "key": option["key"],
                    "label": option["label"],
                    "name": option["name"],
                    "limit": option["limit"],
                    "amount": option["amount"],
                    "available": option["available"],
                    "reason": option["reason"],
                    "parts": [self._odin_part(line) for line in option["lines"]],
                } for option in group["options"]],
            } for group in offer["groups"]],
            "charges": sum(line.subtotal for line in offer["charges"]),
        }

    @api.model
    def _odin_currency_json(self):
        currency = self.env.company.currency_id
        return {
            "id": currency.id,
            "name": currency.name,
            "symbol": currency.symbol,
            "position": currency.position,
            "digits": currency.decimal_places,
        }

    @api.model
    def get_setup(self, lead_id=False):
        """Everything the first step needs: insurers, plans, sources."""
        templates = self.env["sale.order.template"].search([
            ("odin_medical", "=", True),
            ("company_id", "in", [False, self.env.company.id]),
        ]).sorted(lambda t: (t.odin_inpatient_limit, t.sequence, t.id))
        insurers = []
        for template in templates:
            name = template._odin_insurer_name()
            if name not in insurers:
                insurers.append(name)
        lead = self.env["crm.lead"].browse(int(lead_id)).exists() if lead_id else False
        return {
            "currency": self._odin_currency_json(),
            "insurers": insurers,
            "plans": [{
                "id": template.id,
                "name": template.name,
                "insurer": template._odin_insurer_name(),
                "limit": template.odin_inpatient_limit,
                "type": template.odin_plan_type,
            } for template in templates],
            "sources": self.env["utm.source"].search_read([], ["name"], limit=80, order="name"),
            "lead": self._odin_lead_json(lead) if lead else False,
        }

    @api.model
    def search_plans(self, params):
        """Step 2: every plan the cover details ask for, priced for the family."""
        cover = params.get("cover") or {}
        household = self._odin_household(cover)
        book = RateBook(self.env, self.env.company)
        plans, unavailable = [], []
        for template in self._odin_templates(cover):
            data = self._odin_plan_json(template._odin_offer(household, book))
            (plans if data["available"] else unavailable).append(data)
        return {
            "household": {
                "headcount": household.headcount,
                "principal": household.principal.age if household.principal else None,
                "spouse": next((m.age for m in household.members if m.role == "spouse"), None),
                "children": len(household.children),
            },
            "plans": plans,
            "unavailable": unavailable,
        }

    # -- the quotation, built the same way for the preview and for real ----

    @api.model
    def _odin_selected(self, offer, selection):
        """The options ticked on one plan, checked against what it offers."""
        wanted = set(selection.get("options") or [])
        chosen = {}
        for group in offer["groups"]:
            picked = [o for o in group["options"] if o["key"] in wanted]
            if not picked:
                continue
            option = picked[0]
            if not option["available"]:
                raise UserError(_(
                    "%(plan)s: %(benefit)s %(option)s cannot be quoted for this "
                    "family. %(reason)s",
                    plan=offer["template"].name, benefit=group["label"],
                    option=option["label"] or option["name"], reason=option["reason"]))
            chosen[group["key"]] = option
        for group in offer["groups"]:
            if group["key"] not in chosen or not group["requires"]:
                continue
            needed = [g for g in offer["groups"] if g["benefit_key"] == group["requires"]]
            if needed and not any(g["key"] in chosen for g in needed):
                raise UserError(_(
                    "%(plan)s: %(benefit)s is only sold together with %(needed)s cover. "
                    "Add %(needed)s, or remove %(benefit)s.",
                    plan=offer["template"].name, benefit=group["label"],
                    needed=needed[0]["label"]))
        return chosen

    @api.model
    def _odin_taxes(self, product, fiscal_position):
        taxes = product.taxes_id._filter_taxes_by_company(self.env.company)
        return fiscal_position.map_tax(taxes) if fiscal_position else taxes

    @api.model
    def _odin_product_line(self, line, fiscal_position, sequence):
        return {
            "sequence": sequence,
            "product_id": line.product_id.id,
            "product_uom_qty": line.qty,
            "price_unit": line.price_unit,
            "tax_ids": [Command.set(self._odin_taxes(line.product_id, fiscal_position).ids)],
        }

    @api.model
    def _odin_heading_line(self, template_line, sequence):
        values = template_line._prepare_order_line_values()
        # Not optional on the order, whatever the template says: an optional
        # section lets the client change quantities on the portal, and these
        # lines are priced for this family -- five people's dental is not
        # theirs to make one.
        values.update(sequence=sequence, product_uom_qty=0, is_optional=False)
        values.pop("product_id", None)
        values.pop("product_uom_id", None)
        return values

    @api.model
    def _odin_section(self, name, key, sequence):
        return {
            "display_type": "line_section",
            "name": name,
            "benefit_label": name,
            "benefit_key": key,
            "sequence": sequence,
        }

    @api.model
    def _odin_discount_lines(self, premium_lines, discount, fiscal_position):
        """The discount as Odoo's own global discount lines.

        The same lines Sales ▸ Discount makes: one negative line per set of
        taxes, carrying its share of them, so the levies are worked out on the
        premium after discount. Only cover is discounted; stamp duty and the
        policy charge are not the broker's to discount.
        """
        if not discount or not premium_lines:
            return []
        AccountTax = self.env["account.tax"]
        company = self.env.company
        product = self._odin_discount_product()
        base_lines = [
            AccountTax._prepare_base_line_for_taxes_computation(
                {},
                product_id=line.product_id,
                tax_ids=self._odin_taxes(line.product_id, fiscal_position),
                price_unit=line.price_unit,
                quantity=line.qty,
                currency_id=company.currency_id,
                rate=1.0,
            )
            for line in premium_lines
        ]
        AccountTax._add_tax_details_in_base_lines(base_lines, company)
        AccountTax._round_base_lines_tax_details(base_lines, company)
        kind, amount = discount
        premium = sum(line.subtotal for line in premium_lines)
        if company.currency_id.is_zero(premium):
            raise UserError(_("There is no premium to discount on this plan."))
        if kind == "fixed" and company.currency_id.compare_amounts(amount, premium) > 0:
            raise UserError(_("The discount is more than the premium it applies to."))
        # Odoo's fixed-amount discount takes the amount off the total *with*
        # tax; a broker's discount comes off the premium, with the levies
        # worked out after it. So an amount is applied as its share of the
        # premium: 1,000 off 50,052 is 1,000 off, and the levies follow.
        percent = amount if kind == "percent" else amount * 100.0 / premium
        discount_lines = AccountTax._prepare_global_discount_lines(
            base_lines=base_lines,
            company=company,
            amount_type="percent",
            amount=percent,
            computation_key=DISCOUNT_KEY,
            grouping_function=lambda base_line: {"product_id": product},
        )
        name = (_("Discount %s%%", ("%g" % amount)) if kind == "percent"
                else _("Discount"))
        return [{
            "name": name,
            "product_id": base_line["product_id"].id,
            "price_unit": base_line["price_unit"],
            "technical_price_unit": 0,
            "product_uom_qty": base_line["quantity"],
            "tax_ids": [Command.set(base_line["tax_ids"].ids)],
            "extra_tax_data": AccountTax._export_base_line_extra_tax_data(base_line),
        } for base_line in discount_lines]

    @api.model
    def _odin_discount_product(self):
        return (self.env.company.sale_discount_product_id
                or self.env.ref("odin_medical_quote.product_discount"))

    @api.model
    def _odin_policy_product(self):
        return self.env.ref("odin_medical_quote.product_policy_charge")

    @api.model
    def _odin_order_lines(self, offer, selection, fiscal_position=None):
        """Order lines for one plan, in the template's order.

        Core cover, then each benefit with an option picked -- its heading,
        the option priced for the family, and its notes -- then the discount,
        the template's charges with the policy charge after them, and the
        payment terms. Benefits nobody picked are left out: the client reads
        what they are being offered, not the whole rate card.
        """
        fiscal_position = fiscal_position or self.env["account.fiscal.position"]
        chosen = self._odin_selected(offer, selection)
        discount = self._odin_discount(selection.get("discount"))
        policy = self._odin_number(selection.get("policy_charge"), _("Policy charge"))
        lines = []

        def add(values):
            values["sequence"] = len(lines) * 10 + 10
            lines.append(values)

        premium = list(offer["core"])
        for group in offer["groups"]:
            if group["key"] in chosen:
                premium += chosen[group["key"]]["lines"]

        def add_discount():
            discount_lines = self._odin_discount_lines(premium, discount, fiscal_position)
            if discount_lines:
                add(self._odin_section(_("Discount"), "discount", 0))
                for values in discount_lines:
                    add(values)

        def add_policy_charge():
            if policy:
                product = self._odin_policy_product()
                add(self._odin_section(_("Policy charge"), "policy_charge", 0))
                add({
                    "product_id": product.id,
                    "product_uom_qty": 1,
                    "price_unit": policy,
                    "tax_ids": [Command.set(self._odin_taxes(product, fiscal_position).ids)],
                })

        discount_done = policy_done = False
        for block in offer["blocks"]:
            if block["kind"] == "charges" and not discount_done:
                add_discount()
                discount_done = True
            if block["kind"] == "optional":
                offered = [g for g in block["offered"] if g["key"] in chosen]
                if not offered:
                    continue
                if block["section"]:
                    heading = self._odin_heading_line(block["section"], 0)
                    # "Optional – outpatient cover (per person)" names no limit:
                    # the tier picked is the limit, and the client reports
                    # print and compare it from the section.
                    if len(block["offered"]) == 1 and chosen[offered[0]["key"]]["limit"]:
                        heading["cover_limit"] = chosen[offered[0]["key"]]["limit"]
                    add(heading)
                for group in offered:
                    anchor = group["group"]["anchor"]
                    if anchor != block["section"] and anchor.display_type == "line_subsection":
                        add(self._odin_heading_line(anchor, 0))
                    for line in chosen[group["key"]]["lines"]:
                        add(self._odin_product_line(line, fiscal_position, 0))
            else:
                if block["section"]:
                    add(self._odin_heading_line(block["section"], 0))
                for line in block["priced"]:
                    add(self._odin_product_line(line, fiscal_position, 0))
            for note in block["notes"]:
                add(self._odin_heading_line(note, 0))
            if block["key"] == "statutory_charges" and not policy_done:
                add_policy_charge()
                policy_done = True
        if not discount_done:
            add_discount()
        if not policy_done:
            add_policy_charge()
        return lines

    @api.model
    def _odin_preview(self, offer, selection):
        """The quotation for one plan as an unsaved order, to read totals from."""
        lines = self._odin_order_lines(offer, selection)
        return self.env["sale.order"].new({
            "company_id": self.env.company.id,
            "sale_order_template_id": offer["template"].id,
            "order_line": [Command.create(values) for values in lines],
        })

    @api.model
    def _odin_totals(self, order, offer):
        """What a plan card shows, read off the order itself."""
        products = order.order_line.filtered(lambda l: not l.display_type)
        discount = products.filtered(lambda l: l._is_global_discount())
        policy = products.filtered(lambda l: l.product_id == self._odin_policy_product())
        charges_products = {line.product_id for line in offer["charges"]}
        charges = products.filtered(lambda l: l.product_id in charges_products)
        premium = products - discount - policy - charges
        total = order.amount_total
        return {
            "premium": sum(premium.mapped("price_subtotal")),
            "discount": -sum(discount.mapped("price_subtotal")),
            "taxes": order.amount_tax,
            "charges": sum(charges.mapped("price_subtotal")),
            "levies": order.amount_tax + sum(charges.mapped("price_subtotal")),
            "policy_charge": sum(policy.mapped("price_subtotal")),
            "untaxed": order.amount_untaxed,
            "total": total,
            "instalments": [{
                "name": plan.display_name,
                "months": schedule["months"],
                "deposit": schedule["deposit"],
                "instalment": schedule["instalment"],
                "payable": schedule["payable"],
            } for plan in self.env["odin.quote.instalment.plan"].search(
                [("company_id", "=", self.env.company.id)])
              for schedule in [plan.schedule(total)]],
        }

    @api.model
    def compute_totals(self, params):
        """Step 3: the totals of each chosen plan, as the quotation will have
        them. A plan whose discount does not make sense reports its own error
        rather than blanking every card."""
        household = self._odin_household(params.get("cover") or {})
        book = RateBook(self.env, self.env.company)
        result = {}
        for selection in params.get("plans") or []:
            template = self.env["sale.order.template"].browse(int(selection["template_id"]))
            offer = template._odin_offer(household, book)
            try:
                order = self._odin_preview(offer, selection)
                result[str(template.id)] = self._odin_totals(order, offer)
            except UserError as error:
                result[str(template.id)] = {"error": error.args[0]}
        return result

    # -- finding the client -----------------------------------------------

    @api.model
    def _odin_partner_json(self, partner):
        return {
            "id": partner.id,
            "name": partner.display_name,
            "phone": partner.phone or "",
            "email": partner.email or "",
        }

    @api.model
    def _odin_lead_json(self, lead):
        return {
            "id": lead.id,
            "name": lead.name,
            "type": lead.type,
            "partner_id": lead.partner_id.id,
            "contact": lead.partner_id.display_name or lead.contact_name or lead.partner_name or "",
            "phone": lead.phone or "",
            "email": lead.email_from or "",
            "stage": lead.stage_id.name or "",
            "source_id": lead.source_id.id,
        }

    @api.model
    def _odin_phone_domain(self, text):
        digits = re.sub(r"\D", "", text or "")
        if len(digits) < 6:
            return []
        # Stored numbers are international (+2547...), typed ones are often
        # local (07...): the last nine digits are the same either way.
        return [("phone_mobile_search", "ilike", digits[-9:])]

    @api.model
    def find_contacts(self, query):
        """Existing customers matching a name, phone number or email."""
        query = (query or "").strip()
        if len(query) < 2:
            return []
        domain = [("name", "ilike", query), ("email", "ilike", query)]
        domain += self._odin_phone_domain(query)
        partners = self.env["res.partner"].search(
            ["|"] * (len(domain) - 1) + domain, limit=8)
        return [self._odin_partner_json(partner) for partner in partners]

    @api.model
    def find_leads(self, query):
        """Open leads and opportunities matching a name, contact or number."""
        query = (query or "").strip()
        domain = [("name", "ilike", query), ("contact_name", "ilike", query),
                  ("partner_name", "ilike", query), ("email_from", "ilike", query)]
        domain += self._odin_phone_domain(query)
        leads = self.env["crm.lead"].search(
            [("won_status", "!=", "won")] + ["|"] * (len(domain) - 1) + domain,
            limit=8, order="write_date desc")
        return [self._odin_lead_json(lead) for lead in leads]

    @api.model
    def partner_leads(self, partner_id):
        """Open opportunities of a customer, so a new quote can join one
        instead of opening a second opportunity for the same sale."""
        partner = self.env["res.partner"].browse(int(partner_id or 0)).exists()
        if not partner:
            return []
        leads = self.env["crm.lead"].search([
            ("partner_id", "child_of", partner.commercial_partner_id.id),
            ("won_status", "!=", "won"),
        ], limit=5, order="write_date desc")
        return [self._odin_lead_json(lead) for lead in leads]

    @api.model
    def match_prospect(self, name, phone, email):
        """Contacts a 'new' prospect may already be: same email or number."""
        domain = []
        if email and "@" in email:
            domain.append(("email", "=ilike", email.strip()))
        domain += self._odin_phone_domain(phone)
        if not domain:
            return []
        partners = self.env["res.partner"].search(
            ["|"] * (len(domain) - 1) + domain, limit=3)
        return [self._odin_partner_json(partner) for partner in partners]

    @api.model
    def _odin_partner(self, customer):
        """The contact the quote is for, created when they are new."""
        source = customer.get("source") or "new"
        if source == "existing" or (source == "new" and customer.get("partner_id")):
            partner = self.env["res.partner"].browse(int(customer.get("partner_id") or 0)).exists()
            if not partner:
                raise UserError(_("Choose the customer this quote is for."))
            return partner
        if source == "lead":
            lead = self._odin_lead(customer)
            if not lead.partner_id:
                lead._handle_partner_assignment(create_missing=True)
            return lead.partner_id
        name = (customer.get("name") or "").strip()
        phone = (customer.get("phone") or "").strip()
        email = (customer.get("email") or "").strip()
        if not name:
            raise UserError(_("Enter the prospect's name."))
        if not phone and not email:
            raise UserError(_(
                "Enter a phone number or an email for %s, so the quote can reach them.",
                name))
        country = self.env.company.country_id
        return self.env["res.partner"].create({
            "name": name,
            "phone": phone and (
                self.env["res.partner"]._phone_format(number=phone, country=country) or phone),
            "email": email or False,
            "country_id": country.id,
        })

    @api.model
    def _odin_lead(self, customer):
        lead = self.env["crm.lead"].browse(int(customer.get("lead_id") or 0)).exists()
        if not lead:
            raise UserError(_("Choose the lead or opportunity this quote is for."))
        return lead

    @api.model
    def _odin_opportunity(self, customer, partner, household):
        """The CRM opportunity the quotations belong to.

        A lead picked from CRM is converted to an opportunity for this
        customer, the way Convert to opportunity would. Otherwise a new
        opportunity is opened in the agent's pipeline.
        """
        if (customer.get("source") or "new") == "lead":
            lead = self._odin_lead(customer)
            if lead.type == "lead":
                lead.convert_opportunity(partner)
            elif not lead.partner_id:
                lead.partner_id = partner
            if not lead.user_id:
                # Unassigned until now: it is the quoting agent's to follow up.
                lead.user_id = self.env.user
            return lead
        people = household.headcount
        return self.env["crm.lead"].create({
            "name": _("Medical cover – %(name)s (%(count)s)",
                      name=partner.name,
                      count=_("1 person") if people == 1 else _("%s people", people)),
            "type": "opportunity",
            "partner_id": partner.id,
            "user_id": self.env.uid,
            "source_id": int(customer.get("source_id") or 0) or False,
        })

    # -- saving -----------------------------------------------------------

    @api.model
    def _odin_members(self, household):
        return [Command.clear()] + [Command.create({
            "name": member.label,
            "age": member.age,
            "role": member.role,
            "sequence": (index + 1) * 10,
        }) for index, member in enumerate(household.members)]

    @api.model
    def _odin_details(self, household):
        details = []
        if household.principal:
            details.append((_("Principal"), _("%s years", household.principal.age)))
        spouse = next((m for m in household.members if m.role == "spouse"), None)
        if spouse:
            details.append((_("Spouse"), _("%s years", spouse.age)))
        if household.children:
            details.append((_("Children"), str(len(household.children))))
        details.append((_("People covered"), str(household.headcount)))
        return [Command.clear()] + [
            Command.create({"label": label, "value": value, "sequence": (index + 1) * 10})
            for index, (label, value) in enumerate(details)
        ]

    @api.model
    def _odin_clean_params(self, params):
        """What is kept on the comparison to reopen the quote later."""
        kept = copy.deepcopy({key: params.get(key) for key in ("customer", "cover", "plans")})
        return kept

    @api.model
    def save_quote(self, params):
        """Save the quote: contact, opportunity, quotations, comparison.

        Saving again from a reopened quote updates it in place: quotations
        still in draft are rebuilt, plans added get a quotation, plans taken
        off have theirs cancelled. A quote the client has accepted is final.
        """
        Comparison = self.env["odin.quote.comparison"]
        comparison = Comparison.browse(int(params.get("comparison_id") or 0)).exists()
        if comparison and comparison.state == "accepted":
            raise UserError(_(
                "%s has been accepted, so it can no longer change. Start a new quote.",
                comparison.name))
        confirmed = comparison.plan_ids.order_id.filtered(lambda o: o.state == "sale")
        if confirmed:
            raise UserError(_(
                "%(order)s on this quote is already confirmed, so the quote can no "
                "longer change. Use Client accepted to finish it, or start a new quote.",
                order=confirmed[0].name))
        selections = params.get("plans") or []
        if not selections:
            raise UserError(_("Select at least one plan to quote."))
        cover = params.get("cover") or {}
        customer = params.get("customer") or {}
        household = self._odin_household(cover)
        book = RateBook(self.env, self.env.company)
        priced = []
        for selection in selections:
            template = self.env["sale.order.template"].browse(
                int(selection.get("template_id") or 0)).exists()
            if not template:
                raise UserError(_("A plan on this quote no longer exists. Search again."))
            offer = template._odin_offer(household, book)
            if not offer["available"]:
                raise UserError(_("%(plan)s cannot be quoted for this family: %(reason)s",
                                  plan=template.name, reason=offer["reason"]))
            self._odin_selected(offer, selection)
            priced.append((template, offer, selection))

        if comparison:
            partner, lead = comparison.partner_id, comparison.opportunity_id
        else:
            partner = self._odin_partner(customer)
            lead = self._odin_opportunity(customer, partner, household)
        fiscal_position = self.env["account.fiscal.position"].with_company(
            self.env.company)._get_fiscal_position(partner)

        # What sale_crm's New Quotation carries over from the opportunity;
        # anything the opportunity leaves empty is left to the order's own
        # defaults rather than forced blank.
        header = {key: value for key, value in {
            "partner_id": partner.id,
            "company_id": self.env.company.id,
            "opportunity_id": lead.id,
            "user_id": (lead.user_id or self.env.user).id,
            "team_id": lead.team_id.id,
            "campaign_id": lead.campaign_id.id,
            "medium_id": lead.medium_id.id,
            "source_id": lead.source_id.id,
            "tag_ids": [Command.set(lead.tag_ids.ids)] if lead.tag_ids else False,
        }.items() if value}
        # Only the plans this screen put on the quote are rebuilt or taken
        # off. A quotation someone added to the comparison by hand is not the
        # screen's to cancel, and stays as it is.
        managed = {int(p.get("template_id") or 0)
                   for p in (comparison.odin_builder_params or {}).get("plans", [])}
        existing = {plan.order_id.sale_order_template_id.id: plan
                    for plan in comparison.plan_ids
                    if plan.order_id.sale_order_template_id.id in managed}
        SaleOrder = self.env["sale.order"]
        Plan = self.env["odin.quote.comparison.plan"]
        plans = []
        replaced = Plan
        for index, (template, offer, selection) in enumerate(priced):
            lines = [Command.create(values) for values in
                     self._odin_order_lines(offer, selection, fiscal_position)]
            plan = existing.pop(template.id, None)
            if plan and plan.order_id.state in EDITABLE_ORDER_STATES:
                order = plan.order_id
                order.order_line = [Command.clear()] + lines
            else:
                # Cancelled by hand since it was quoted: it gets a fresh
                # quotation rather than being brought back.
                replaced |= plan or Plan
                order = SaleOrder.create(dict(
                    header, sale_order_template_id=template.id, order_line=lines,
                    origin=comparison.name if comparison else False))
                plan = False
            plans.append((plan, order, index, bool(selection.get("recommended"))))

        removed = Plan.concat(*existing.values())
        if removed:
            removed.order_id.filtered(
                lambda o: o.state in EDITABLE_ORDER_STATES)._action_cancel()

        plan_commands = [Command.unlink(plan.id) for plan in removed | replaced]
        for plan, order, index, recommended in plans:
            values = {"sequence": (index + 1) * 10, "highlight": recommended}
            if plan:
                plan_commands.append(Command.update(plan.id, values))
            else:
                plan_commands.append(Command.create(dict(values, order_id=order.id)))
        orders = SaleOrder.concat(*[order for _plan, order, _i, _r in plans])
        values = {
            "plan_ids": plan_commands,
            "member_ids": self._odin_members(household),
            "detail_ids": self._odin_details(household),
            "odin_builder_params": self._odin_clean_params(params),
            "template_ids": [Command.set(orders.sale_order_template_id.ids)],
        }
        if comparison:
            if comparison.state == "declined":
                # Quoting again after a no: the client is back in play.
                values["state"] = "draft"
                if lead and not lead.active:
                    lead.action_restore()
            comparison.write(values)
        else:
            days = [t.number_of_days for t, _o, _s in priced if t.number_of_days]
            comparison = Comparison.create(dict(
                values,
                partner_id=partner.id,
                opportunity_id=lead.id if lead else False,
                title=_("Medical Insurance"),
                note=orders[:1].note,
                validity_date=fields.Date.context_today(self) + relativedelta(days=min(days))
                if days else False,
                instalment_plan_ids=[Command.set(self.env["odin.quote.instalment.plan"].search(
                    [("company_id", "=", self.env.company.id)]).ids)],
            ))
            orders.filtered(lambda o: not o.origin).origin = comparison.name

        created = not params.get("comparison_id")
        if lead:
            recommended = [order for _p, order, _i, rec in plans if rec]
            basis = recommended[0] if recommended else min(orders, key=lambda o: o.amount_untaxed)
            lead.expected_revenue = basis.amount_untaxed
            if created and not lead.date_deadline and comparison.validity_date:
                # The day the quote lapses is when the opportunity is decided.
                lead.date_deadline = comparison.validity_date
            if created and not lead.activity_ids:
                # A quote nobody chases is a quote lost: the opportunity gets
                # its next step unless it already has one.
                lead.activity_schedule(
                    "mail.mail_activity_data_todo",
                    date_deadline=fields.Date.context_today(self) + relativedelta(
                        days=FOLLOW_UP_DAYS),
                    summary=_("Follow up on medical quote %s", comparison.name),
                    user_id=(lead.user_id or self.env.user).id,
                )
        self._odin_log(comparison, lead, orders, household, created=created)
        return self.load_quote(comparison.id)

    @api.model
    def _odin_log(self, comparison, lead, orders, household, created):
        currency = self.env.company.currency_id
        rows = Markup("").join(
            Markup("<li>%s: %s</li>") % (
                order.sale_order_template_id.name or order.name,
                currency.format(order.amount_total),
            ) for order in orders)
        body = Markup("<p>%s</p><ul>%s</ul>") % (
            _("Medical quote %(state)s for %(people)s, %(plans)s:",
              state=_("saved") if created else _("updated"),
              people=_("1 person") if household.headcount == 1
              else _("%s people", household.headcount),
              plans=_("1 plan") if len(orders) == 1 else _("%s plans", len(orders))),
            rows)
        comparison.message_post(body=body)
        if lead:
            link = Markup('<a href="#" data-oe-model="%s" data-oe-id="%s">%s</a>') % (
                comparison._name, comparison.id, comparison.name)
            lead.message_post(body=body + Markup("<p>%s %s</p>") % (_("Quote:"), link))

    # -- reopening --------------------------------------------------------

    @api.model
    def load_quote(self, comparison_id):
        """A saved quote, as the screen reopens it and the success panel
        shows it."""
        comparison = self.env["odin.quote.comparison"].browse(int(comparison_id)).exists()
        if not comparison:
            raise UserError(_("This quote no longer exists."))
        try:
            comparison.check_access("read")
        except AccessError:
            raise UserError(_("You cannot open this quote.")) from None
        orders = comparison.plan_ids.sorted(lambda p: (p.sequence, p.id))
        return {
            "comparison": {
                "id": comparison.id,
                "name": comparison.name,
                "state": comparison.state,
                "accepted_order_id": comparison.odin_accepted_order_id.id,
                "handler": comparison.odin_handler_id.name or "",
            },
            "params": comparison.odin_builder_params or False,
            "partner": self._odin_partner_json(comparison.partner_id),
            "lead": self._odin_lead_json(comparison.opportunity_id)
            if comparison.opportunity_id else False,
            "orders": [{
                "id": plan.order_id.id,
                "name": plan.order_id.name,
                "template_id": plan.order_id.sale_order_template_id.id,
                "plan": plan.order_id.sale_order_template_id.name or plan.name,
                "state": plan.order_id.state,
                "total": plan.order_id.amount_total,
                "recommended": plan.highlight,
            } for plan in orders],
        }
