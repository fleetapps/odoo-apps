"""The online quote page's server side.

A stranger answers three questions -- who needs cover and their ages, what
else to cover, where to send the quote -- and gets the plans that fit, priced
by the same engine as Add Medical Quote, in words they understand. Saving
makes the same records an agent's quote does (contact, opportunity,
quotations, the quote), so from there it is an ordinary quote for the office.

Who follows it up: the agent whose link it came by, or the salesperson of
the opportunity the link was sent from; otherwise whoever already has an
open opportunity with that phone or email; otherwise the person set in
Settings; otherwise nobody yet -- it waits in the team's pool (an ad, the
website) until someone presses Assign to me.

Nothing a visitor types can reveal what is already in the database: a quote
from the page always gets a new contact, however familiar the phone number,
and only a link an agent sent (signed with the database secret) attaches it
to an existing opportunity.

Motor (odin_motor_quote_online) inherits all of this; what differs between
lines is in the methods marked "per line".
"""

import logging
import re
from datetime import timedelta
from urllib.parse import quote as url_quote

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import LazyTranslate, consteq, email_normalize, formatLang, format_datetime
from odoo.tools import hmac as signature

from .quote_comparison import CONTACT_BY, CONTACT_WHEN

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)

#: The cover a stranger can ask for, in plain words. Anything else a plan
#: offers (riders and their brand names) is left to the advisor.
BENEFITS = {
    "outpatient": (_lt("Doctor visits & medicines"),
                   _lt("Clinic visits, prescriptions and tests when you are not admitted")),
    "maternity": (_lt("Pregnancy & birth"), _lt("Antenatal care and delivery")),
    "dental": (_lt("Dental care"), _lt("Check-ups, fillings and extractions")),
    "optical": (_lt("Eyes & glasses"), _lt("Eye tests, glasses and lenses")),
}
HOSPITAL = _lt("Hospital stays")
WHO = ("me", "couple", "me_kids", "family", "kids")
MAX_PLANS = 3
MAX_CHILDREN = 8
#: Spam guards, overridable as system parameters.
PER_PHONE_PER_DAY = "odin_medical_quote_online.max_per_phone_day"
PER_HOUR = "odin_medical_quote_online.max_per_hour"


class MedicalQuoteOnline(models.AbstractModel):
    _name = "odin.medical.quote.online"
    _description = "Online medical quote"

    # -- signed links -------------------------------------------------------

    @api.model
    def _sign(self, scope, record_id):
        return signature(self.env(su=True), scope, record_id)[:24]

    @api.model
    def _token(self, scope, record_id):
        return "%s-%s" % (record_id, self._sign(scope, record_id))

    @api.model
    def _verify(self, scope, token):
        """The record id ``token`` was signed for, or 0."""
        record_id, _sep, sign = str(token or "").partition("-")
        if not record_id.isdigit() or not sign:
            return 0
        return int(record_id) if consteq(sign, self._sign(scope, int(record_id))) else 0

    @api.model
    def lead_token(self, lead):
        return self._token("odin-online-lead", lead.id)

    @api.model
    def agent_token(self, user):
        return self._token("odin-online-agent", user.id)

    @api.model
    def _lead_from_token(self, token):
        """The opportunity an agent's personal link is for, while still open."""
        lead = self.env["crm.lead"].sudo().browse(self._verify("odin-online-lead", token)).exists()
        return lead if lead and lead.active and lead.won_status != "won" else lead.browse()

    @api.model
    def _agent_from_token(self, token):
        user = self.env["res.users"].sudo().browse(self._verify("odin-online-agent", token)).exists()
        return user if user and user.active and not user.share else user.browse()

    @api.model
    def _quote_from_token(self, quote_id, token):
        comparison = self.env["odin.quote.comparison"].sudo().browse(
            int(quote_id or 0)).exists()
        return comparison if comparison and comparison._odin_online_check(token) else comparison.browse()

    # -- the family -----------------------------------------------------------

    @api.model
    def _age(self, value, adult, who):
        try:
            age = int(value)
        except (TypeError, ValueError):
            raise UserError(_("Enter %s.", who)) from None
        low = 18 if adult else 0
        if not low <= age <= 99:
            raise UserError(_("%(who)s should be between %(low)s and 99.", who=who, low=low))
        return age

    @api.model
    def _cover(self, family):
        """The cover details Add Medical Quote works from, out of the answers
        on the page. Ages, not dates of birth: a date of birth six months
        past the birthday gives the same age, and the advisor confirms the
        dates before the quote is final."""
        family = family or {}
        who = family.get("who")
        if who not in WHO:
            raise UserError(_("Tell us who needs cover."))
        today = fields.Date.context_today(self)

        def born(age):
            return fields.Date.to_string(today - relativedelta(years=age, months=6))

        cover = {"ages_only": True, "include_spouse": who in ("couple", "family"),
                 "children": 0, "children_only": who == "kids"}
        if who != "kids":
            age = self._age(family.get("age"), True, _("your age"))
            cover.update(principal_age=age, principal_dob=born(age))
        if cover["include_spouse"]:
            age = self._age(family.get("partner_age"), True, _("your partner's age"))
            cover.update(spouse_age=age, spouse_dob=born(age))
        if who in ("me_kids", "family", "kids"):
            try:
                children = int(family.get("children") or 0)
            except (TypeError, ValueError):
                children = 0
            if not 1 <= children <= MAX_CHILDREN:
                raise UserError(_("Enter how many children need cover (1 to %s).", MAX_CHILDREN))
            cover["children"] = children
        return cover

    @api.model
    def _family(self, cover):
        """The page's answers back out of saved cover details."""
        cover = cover or {}
        spouse, children = cover.get("include_spouse"), int(cover.get("children") or 0)
        if cover.get("children_only"):
            who = "kids"
        else:
            who = "family" if spouse and children else "couple" if spouse else (
                "me_kids" if children else "me")

        def age(key):
            if cover.get("%s_age" % key):
                return int(cover["%s_age" % key])
            born = fields.Date.to_date(cover.get("%s_dob" % key))
            return relativedelta(fields.Date.context_today(self), born).years if born else ""

        return {"who": who, "age": age("principal"), "partner_age": age("spouse") if spouse else "",
                "children": children or ""}

    @api.model
    def _headcount(self, cover):
        adults = (0 if cover.get("children_only") else 1) + (1 if cover.get("include_spouse") else 0)
        return adults + int(cover.get("children") or 0)

    # -- plans and prices -----------------------------------------------------

    @api.model
    def _quoter(self):
        """Add Medical Quote's engine, limited to the plans shown online."""
        return self.env["odin.medical.quote"].sudo().with_context(odin_online=True)

    @api.model
    def _picks(self, plan, wants, tiers):
        """What goes on one plan: each benefit asked for, at the tier picked
        or the cheapest, and any benefit it cannot be had without -- added
        and marked, rather than a rule the visitor has to discover. None
        when the plan does not offer what was asked."""
        groups = {}
        for group in plan["groups"]:
            options = [o for o in group["options"] if o["available"]]
            if group["benefit_key"] in BENEFITS and options and group["benefit_key"] not in groups:
                groups[group["benefit_key"]] = (group, options)
        needed = {}
        for key in wants:
            if key not in groups:
                return None
            needed[key] = False
            required = groups[key][0]["requires"]
            if required and required not in wants:
                if required not in groups:
                    return None
                needed.setdefault(required, key)
        picks = {}
        for key in BENEFITS:
            if key not in needed:
                continue
            group, options = groups[key]
            option = next((o for o in options if o["key"] == tiers.get(key)), None) or min(
                options, key=lambda o: o["amount"])
            picks[key] = {"group": group, "options": options, "option": option,
                          "for": needed[key]}
        return picks

    @api.model
    def _plan_name(self, plan):
        # "Plan 2 (KES 300,000) – Below 70": the age variant is the
        # engine's business, not the visitor's.
        return re.sub(r"\s*[–-]\s*(Below|Above)\s+70\s*$", "", plan["name"] or "", flags=re.I)

    @api.model
    def _public_plan(self, plan, picks, totals):
        instalments = sorted(totals.get("instalments") or [], key=lambda i: i["months"])
        monthly = instalments[-1] if instalments else None
        return {
            "id": plan["id"],
            "insurer": plan["insurer"],
            "name": self._plan_name(plan),
            "product": plan["product"] if plan["product"] != plan["insurer"] else "",
            "limit": plan["limit"],
            "hospital": plan["core"]["amount"],
            "benefits": [{
                "key": key,
                "label": str(BENEFITS[key][0]),
                "help": str(BENEFITS[key][1]),
                "for": str(BENEFITS[pick["for"]][0]) if pick["for"] else "",
                "option": pick["option"]["key"],
                "limit": pick["option"]["limit"],
                "amount": pick["option"]["amount"],
                "options": [{"key": o["key"], "limit": o["limit"], "amount": o["amount"],
                             "label": o["label"] or o["name"]} for o in pick["options"]],
            } for key, pick in picks.items()],
            "premium": totals["premium"],
            "levies": totals["levies"],
            "total": totals["total"],
            "monthly": {
                "months": monthly["months"],
                "deposit": monthly["deposit"],
                "instalment": monthly["instalment"],
            } if monthly else False,
        }

    @api.model
    def options(self, family, wants=None, tiers=None, only=None):
        """The plans that cover this family and what was asked for, cheapest
        first, each priced in full (levies and stamp duty included)."""
        cover = self._cover(family)
        wants = [key for key in BENEFITS if key in (wants or [])]
        tiers = tiers or {}
        only = {int(i) for i in only or []}
        quoter = self._quoter()
        found = quoter.search_plans({"cover": cover})
        offered, picked, selections = set(), [], []
        for plan in found["plans"]:
            offered |= {g["benefit_key"] for g in plan["groups"] if g["benefit_key"] in BENEFITS
                        and any(o["available"] for o in g["options"])}
            if only and plan["id"] not in only:
                continue
            picks = self._picks(plan, wants, tiers.get(str(plan["id"])) or {})
            if picks is None:
                continue
            picked.append((plan, picks))
            selections.append({"template_id": plan["id"],
                               "options": [pick["option"]["key"] for pick in picks.values()]})
        totals = quoter.compute_totals({"cover": cover, "plans": selections}) if selections else {}
        plans = []
        for plan, picks in picked:
            plan_totals = totals.get(str(plan["id"])) or {}
            if plan_totals and "error" not in plan_totals:
                plans.append(self._public_plan(plan, picks, plan_totals))
        plans.sort(key=lambda p: (p["total"], p["id"]))
        return {
            "plans": plans,
            "offered": [key for key in BENEFITS if key in offered],
            "people": self._headcount(cover),
        }

    # -- per line -------------------------------------------------------------
    # Medical here; odin_motor_quote_online overrides these.

    @api.model
    def _odin_type(self):
        return "medical"

    @api.model
    def _odin_path(self):
        """Where the page is: /medical-quote."""
        return "/medical-quote"

    @api.model
    def _odin_source_name(self):
        return "Online medical quote"

    @api.model
    def _lead_name(self, partner, cover):
        people = self._headcount(cover)
        return _("Online medical quote – %(name)s (%(people)s)", name=partner.name,
                 people=_("1 person") if people == 1 else _("%s people", people))

    @api.model
    def _share_message(self, hello, link):
        return _("%(hello)s you can see medical cover prices for your family and get your "
                 "quote here, in about two minutes:\n%(link)s", hello=hello, link=link)

    @api.model
    def _note_lines(self, cover):
        """What the agent should check first, on the opportunity."""
        return [_("Ages given, not dates of birth: confirm them before the quote is final.")]

    # -- who handles it -------------------------------------------------------

    @api.model
    def _team(self):
        company = self.env.company.sudo()
        return company.odin_online_team_id or self.env["crm.team"].sudo().search(
            [("company_id", "in", (company.id, False))], limit=1)

    @api.model
    def _returning(self, contact):
        """An open opportunity someone already handles with this phone or
        email: the client of an agent, back through an ad or the website."""
        matches = [("phone_mobile_search", "ilike", contact["digits"][-9:])]
        email = email_normalize(contact["email"]) if contact["email"] else False
        if email:
            matches = ["|", *matches, ("email_normalized", "=", email)]
        return self.env["crm.lead"].sudo().search([
            *matches, ("user_id.active", "=", True), ("user_id.share", "=", False),
            ("won_status", "!=", "won"), ("company_id", "in", (self.env.company.id, False)),
        ], order="write_date desc", limit=1)

    @api.model
    def _assignee(self, lead, agent, contact):
        """Who follows the quote up, and why, or nobody: the team picks it up.

        The salesperson of the opportunity the link was sent from, then the
        agent whose link it is, then whoever has an open opportunity with the
        same phone or email, then the person in Settings."""
        if lead.user_id.active:
            return lead.user_id, ""
        if agent:
            return agent, ""
        returning = self._returning(contact)
        if returning:
            return returning.user_id, _(
                "Assigned to %(user)s, who has %(lead)s open with the same phone or email. "
                "If it is the same person, merge the two.",
                user=returning.user_id.name, lead=returning.name)
        user = self.env.company.sudo().odin_online_user_id
        if user.active and not user.share:
            return user, ""
        return self.env["res.users"], ""

    @api.model
    def _online_tag(self):
        Tag = self.env["crm.tag"].sudo()
        return Tag.search([("name", "=", "Online quote")], limit=1) or Tag.create(
            {"name": "Online quote"})

    @api.model
    def _new_lead(self, partner, cover, user):
        company = self.env.company
        team = company.sudo().odin_online_team_id or self.env["crm.team"].sudo()._get_default_team_id(
            user_id=user.id or None)
        # As OdooBot, so the source, medium and campaign of the link the
        # visitor came by (utm_source=Facebook...) are filled in from its
        # cookies, as they are for any lead a website form makes, and the
        # chatter does not say "Public user" created it.
        lead = self.env["crm.lead"].with_user(SUPERUSER_ID).with_context(
            mail_create_nosubscribe=True).create({
            "name": self._lead_name(partner, cover),
            "type": "opportunity",
            "partner_id": partner.id,
            "user_id": user.id or False,
            "team_id": team.id if team else False,
            "company_id": company.id,
            "tag_ids": [fields.Command.link(self._online_tag().id)],
        })
        if not lead.source_id:
            lead.source_id = self.env["utm.mixin"].sudo()._find_or_create_record(
                "utm.source", self._odin_source_name())
        return lead

    @api.model
    def _schedule(self, lead, user, summary, note=""):
        """A to-do on the opportunity, given by OdooBot so the agent is
        notified (Odoo does not tell you about a to-do you gave yourself)."""
        activity_type = self.env.ref("odin_medical_quote_online.mail_activity_type_online_quote")
        bot = lead.with_user(SUPERUSER_ID)
        bot.activity_ids.filtered(lambda a: a.activity_type_id == activity_type).unlink()
        bot.activity_schedule(
            activity_type_id=activity_type.id, summary=summary, note=note,
            date_deadline=fields.Date.context_today(self), user_id=user.id)

    @api.model
    def _contact_summary(self, partner, contact_by):
        return _("Online quote: contact %(name)s (%(how)s)",
                 name=partner.name, how=dict(CONTACT_BY).get(contact_by or "whatsapp"))

    @api.model
    def _notify_team(self, lead, body):
        """Tell the team a quote is waiting to be picked up, in their Odoo
        inbox (or by email, as each prefers). Nobody in the team: the sales
        managers."""
        team = lead.team_id or self._team()
        users = team.member_ids | team.user_id
        if not users:
            users = self.env.ref("sales_team.group_sale_manager").sudo().user_ids
        users = users.filtered(lambda u: u.active and not u.share)
        self._post(lead, body, partners=users.partner_id)

    @api.model
    def _post(self, record, body, partners=None):
        """A note in the chatter, by OdooBot rather than "Public user"."""
        record.with_user(SUPERUSER_ID).message_post(
            body=body, message_type="comment", subtype_xmlid="mail.mt_note",
            partner_ids=partners.ids if partners else [])

    # -- the quote ------------------------------------------------------------

    @api.model
    def _check_contact(self, contact):
        if contact.get("website"):
            # The honeypot: a field people never see, which robots fill in.
            raise UserError(_("Something went wrong. Please try again."))
        name = " ".join((contact.get("name") or "").split())[:80]
        if not name:
            raise UserError(_("Enter your name."))
        phone = (contact.get("phone") or "").strip()[:30]
        digits = re.sub(r"\D", "", phone)
        if not 9 <= len(digits) <= 15:
            raise UserError(_("Enter a phone number we can reach you on, like 0712 345 678."))
        email = (contact.get("email") or "").strip()[:120]
        if email and not email_normalize(email):
            raise UserError(_("That email address doesn't look right."))
        contact_by = contact.get("contact_by") or "whatsapp"
        if contact_by not in dict(CONTACT_BY):
            contact_by = "whatsapp"
        if contact_by == "email" and not email:
            raise UserError(_("Enter your email, or choose another way to contact you."))
        if not contact.get("consent"):
            raise UserError(_("Tick the box to let us contact you about your quote."))
        return {"name": name, "phone": phone, "digits": digits, "email": email,
                "contact_by": contact_by}

    @api.model
    def _check_chosen(self, chosen):
        chosen = list(dict.fromkeys(int(i) for i in chosen or []))
        if not chosen:
            raise UserError(_("Choose at least one plan."))
        if len(chosen) > MAX_PLANS:
            raise UserError(_("Choose up to %s plans.", MAX_PLANS))
        return chosen

    @api.model
    def _check_caps(self, digits):
        params = self.env["ir.config_parameter"].sudo()
        Comparison = self.env["odin.quote.comparison"].sudo()
        now = fields.Datetime.now()
        per_hour = int(params.get_param(PER_HOUR, 100))
        if per_hour and Comparison.search_count([
                ("odin_channel", "=", "online"),
                ("create_date", ">=", now - timedelta(hours=1))]) >= per_hour:
            raise UserError(_("We're receiving a lot of requests right now. Please try again "
                              "in a few minutes, or chat with us on WhatsApp."))
        per_phone = int(params.get_param(PER_PHONE_PER_DAY, 5))
        if per_phone and Comparison.search_count([
                ("odin_channel", "=", "online"),
                ("create_date", ">=", now - timedelta(days=1)),
                ("partner_id.phone_mobile_search", "ilike", digits[-9:])]) >= per_phone:
            raise UserError(_("You've asked for several quotes today, so an advisor will be in "
                              "touch. You can also chat with us on WhatsApp."))

    @api.model
    def submit(self, family, wants=None, tiers=None, chosen=None, contact=None, tokens=None):
        """Save the visitor's quote and return the link to their page."""
        contact = self._check_contact(contact or {})
        chosen = self._check_chosen(chosen)
        self._check_caps(contact["digits"])
        cover = self._cover(family)
        # Priced again here: nothing the browser says about a price is used.
        priced = {plan["id"]: plan for plan in self.options(family, wants, tiers, only=chosen)["plans"]}
        if any(plan_id not in priced for plan_id in chosen):
            raise UserError(_("A plan you chose is no longer available. Please choose again."))
        return self._save(cover, [{
            "template_id": plan_id,
            "options": [benefit["option"] for benefit in priced[plan_id]["benefits"]],
        } for plan_id in chosen], contact, tokens or {})

    @api.model
    def _save(self, cover, selections, contact, tokens):
        """Save a quote from the page, whatever the line: the contact, the
        opportunity and who follows it up, the quote itself."""
        previous = self._quote_from_token(tokens.get("quote_id"), tokens.get("quote"))
        lead = self._lead_from_token(tokens.get("lead"))
        via = "client" if lead else False
        if not lead and previous.opportunity_id.active and previous.opportunity_id.won_status != "won":
            lead = previous.opportunity_id
        agent = self._agent_from_token(tokens.get("agent"))
        via = via or ("agent" if agent else previous.odin_online_via or "public")
        user, why = self._assignee(lead, agent, contact)
        partner = lead.partner_id or previous.partner_id
        if not partner:
            partner = self._quoter()._odin_partner({
                "source": "new", "name": contact["name"],
                "phone": contact["phone"], "email": contact["email"]})
        else:
            # Their own link: what they add is theirs to add (a WhatsApp
            # contact may have come without a phone number).
            if not partner.email and contact["email"]:
                partner.email = contact["email"]
            if not partner.phone and contact["phone"]:
                partner.phone = contact["phone"]
        if not lead:
            lead = self._new_lead(partner, cover, user)
        else:
            if not lead.partner_id:
                lead.partner_id = partner
            if not lead.user_id and user:
                lead.user_id = user
        owner = lead.user_id
        if owner:
            # Before saving, so the quote does not add a follow-up of its own.
            self._schedule(lead, owner, self._contact_summary(partner, contact["contact_by"]))

        engine = self._quoter()
        engine = engine.with_user(owner).sudo() if owner else engine.with_user(
            SUPERUSER_ID).with_context(odin_unassigned=True)
        saved = engine.save_quote({
            "customer": {"source": "lead", "lead_id": lead.id},
            "cover": cover,
            "plans": selections,
        })
        comparison = self.env["odin.quote.comparison"].sudo().browse(saved["comparison"]["id"])
        comparison.write({"odin_channel": "online", "odin_contact_by": contact["contact_by"],
                          "odin_online_via": via})
        comparison._odin_online_ensure_token()
        self._note(lead, comparison, partner, contact, cover, why)
        if not owner:
            self._notify_team(lead, _(
                "New online quote %(quote)s from %(name)s, waiting to be picked up. Open it and "
                "press Assign to me.", quote=comparison.name, name=partner.name))
        if contact["email"] and partner.email:
            self._email(comparison, owner)
        return {"url": comparison._odin_online_url()}

    @api.model
    def _note(self, lead, comparison, partner, contact, cover, why=""):
        """What the agent needs before calling, on the opportunity."""
        now = format_datetime(self.env, fields.Datetime.now(), dt_format="d MMM y HH:mm")
        lines = [
            _("Made by the client on the online quote page: %s.", comparison.name),
            *self._note_lines(cover),
            _("Prefers to be contacted by %s.", dict(CONTACT_BY)[contact["contact_by"]]),
            _("Agreed to be contacted about this quote on %s.", now),
        ]
        if why:
            lines.append(why)
        matches = [("phone_mobile_search", "ilike", contact["digits"][-9:])]
        if contact["email"]:
            matches = ["|", *matches, ("email", "=ilike", contact["email"])]
        same = self.env["res.partner"].sudo().search([
            ("commercial_partner_id", "!=", partner.commercial_partner_id.id), *matches,
        ], limit=3)
        if same:
            lines.append(_(
                "Already in Contacts with the same phone or email: %s. If it is the same "
                "person, merge the contacts.", ", ".join(same.mapped("display_name"))))
        body = Markup("<br/>").join(lines)
        self._post(lead, body)
        self._post(comparison, body)

    @api.model
    def _email(self, comparison, user):
        """The quote, with its PDF, to the email they gave -- as the agent
        would send it, or from the company while nobody has picked it up."""
        sender = user or self.env["res.users"].browse(SUPERUSER_ID)
        try:
            with self.env.cr.savepoint():
                action = comparison.with_user(sender).sudo().action_odin_send_email()
                self.env["mail.compose.message"].with_user(sender).sudo().with_context(
                    **action["context"]).create({})._action_send_mail()
        except Exception:
            _logger.exception("Online quote %s: the email could not be sent", comparison.id)

    # -- after the quote ------------------------------------------------------

    @api.model
    def request_callback(self, comparison, contact_by, contact_when):
        """Talk to an advisor: the agent gets a to-do for today and the
        opportunity is starred; while nobody has picked it up, the team is
        told again. The quote says how and when."""
        comparison = comparison.sudo()
        contact_by = contact_by if contact_by in dict(CONTACT_BY) else (
            comparison.odin_contact_by or "whatsapp")
        contact_when = contact_when if contact_when in dict(CONTACT_WHEN) else "any"
        now = fields.Datetime.now()
        recent = comparison.odin_callback_requested and (
            now - comparison.odin_callback_requested < timedelta(hours=1))
        comparison.write({"odin_contact_by": contact_by, "odin_contact_when": contact_when,
                          "odin_callback_requested": now})
        if recent:
            return
        how = dict(CONTACT_BY)[contact_by]
        when = dict(CONTACT_WHEN)[contact_when].lower()
        body = _("%(name)s asked to talk to an advisor about %(quote)s: by %(how)s, %(when)s.",
                 name=comparison.partner_id.name, quote=comparison.name, how=how, when=when)
        self._post(comparison, body)
        lead = comparison.opportunity_id
        if not lead:
            return
        if lead.priority < "2":
            lead.priority = "2"
        user = lead.user_id or comparison.user_id
        if not user:
            self._notify_team(lead, _("%s Nobody has picked this quote up yet.", body))
            return
        self._schedule(lead, user, _(
            "Call back %(name)s: %(how)s, %(when)s", name=comparison.partner_id.name,
            how=how, when=when), note=body)
        self._post(lead, body)

    @api.model
    def _status(self, comparison):
        if comparison.state == "accepted":
            return "accepted"
        if comparison.state == "declined" or not comparison._odin_export_plans():
            return "closed"
        if comparison.validity_date and comparison.validity_date < fields.Date.context_today(self):
            return "expired"
        return "open"

    @api.model
    def _plain_sections(self, order, comparison):
        """What a plan covers, in plain words: ``[{label, text}]``."""
        currency = order.currency_id
        rows = []
        for section in order._client_sections():
            if not section["included"] or section["key"] in (
                    "statutory_charges", "payment_options", "discount", "policy_charge"):
                continue
            label = HOSPITAL if section["key"] == "inpatient" else (
                BENEFITS[section["key"]][0] if section["key"] in BENEFITS else section["label"])
            text = _("up to %s a year", self._whole_money(section["limit"], currency)) \
                if section["limit"] else ""
            rows.append({"label": str(label), "text": text})
        return rows

    @api.model
    def _whole_money(self, amount, currency=None):
        """``KES 300,000``: an amount with its currency, no cents."""
        currency = currency or self.env.company.currency_id
        number = formatLang(self.env, amount, digits=0)
        symbol = currency.symbol or currency.name
        return "%s %s" % ((number, symbol) if currency.position == "after" else (symbol, number))

    @api.model
    def _plan_heading(self, order, plan):
        """(insurer, plan name) for the client's page."""
        template = order.sale_order_template_id
        if not template:
            return plan.name, plan.subtitle or order.name
        return template._odin_insurer_name(), self._plan_name(
            {"name": template._odin_heading()[1] or template.name})

    @api.model
    def _quote_texts(self, comparison):
        """The words of the client's page that depend on the line."""
        return {
            "kind": _("medical"),
            "about": comparison.odin_cover_summary,
            "per": _("a year"),
            "monthly": True,
            "fineprint": _(
                "Prices are for a year of cover and include government levies. Your advisor "
                "confirms the cover with the insurer before you pay; the insurer's policy "
                "document sets out exactly what is covered."),
        }

    @api.model
    def _whatsapp(self, company):
        """The company's WhatsApp number in the digits-only form wa.me wants."""
        raw = company.odin_online_whatsapp or company.phone or ""
        digits = re.sub(r"\D", "", raw)
        code = str(company.country_id.phone_code or "")
        if digits and code and not raw.strip().startswith("+") and not digits.startswith(code):
            digits = code + digits.lstrip("0")
        return digits

    @api.model
    def _company_props(self, company):
        return {
            "name": company.name,
            "phone": company.phone or "",
            "whatsapp": self._whatsapp(company),
            "reply": company.odin_online_reply_time or "",
            "privacy": company.odin_online_privacy_url or "",
            "currency": company.currency_id.symbol or company.currency_id.name,
            "currencyAfter": company.currency_id.position == "after",
        }

    @api.model
    def page_props(self, lead_token=None, agent_token=None, quote_id=None, quote_token=None,
                   hints=None):
        """What the online quote page starts with: the company, and for a
        visitor who came by an agent's link or from their own quote, their
        answers filled in. ``hints`` are answers the link itself carries
        (who=family, from a WhatsApp auto-reply), checked like any input."""
        company = self.env.company.sudo()
        prefill, tokens = {}, {}
        agent = self._agent_from_token(agent_token)
        if agent:
            tokens["agent"] = agent_token
        lead = self._lead_from_token(lead_token)
        if lead:
            tokens["lead"] = lead_token
            prefill.update(
                name=lead.partner_id.name or lead.contact_name or "",
                phone=lead.partner_id.phone or lead.phone or "",
                email=lead.partner_id.email or lead.email_from or "")
        previous = self._quote_from_token(quote_id, quote_token)
        if previous and previous.odin_quote_type == self._odin_type():
            tokens.update(quote_id=previous.id, quote=quote_token)
            prefill.update(
                name=previous.partner_id.name or "", phone=previous.partner_id.phone or "",
                email=previous.partner_id.email or "",
                contact_by=previous.odin_contact_by or "whatsapp")
            prefill.update(self._prefill(previous))
        elif hints:
            prefill.update(self._prefill_hints(hints))
        return dict(self._page_setup(), **{
            "company": self._company_props(company),
            "maxPlans": MAX_PLANS,
            "prefill": prefill,
            "tokens": tokens,
        })

    @api.model
    def _page_setup(self):
        """Per line: what the page needs to ask its questions."""
        return {"benefits": [{"key": key, "label": str(label), "help": str(help_text)}
                             for key, (label, help_text) in BENEFITS.items()]}

    @api.model
    def _prefill_hints(self, hints):
        """Per line: answers a link may carry."""
        return {"who": hints["who"]} if hints.get("who") in WHO else {}

    @api.model
    def _prefill(self, previous):
        """Per line: the answers of a quote being changed."""
        cover = (previous.odin_builder_params or {}).get("cover") or {}
        return dict(self._family(cover), wants=sorted({
            section["key"] for plan in previous._odin_export_plans()
            for section in plan.order_id._client_sections()
            if section["included"] and section["key"] in BENEFITS}))

    @api.model
    def quote_values(self, comparison, token):
        """The client's page for their quote."""
        comparison = comparison.sudo()
        company = comparison.company_id
        texts = self._quote_texts(comparison)
        plans = comparison._odin_export_plans() or comparison.plan_ids.sorted(
            lambda p: (p.sequence, p.id))
        instalments = comparison.instalment_plan_ids.sorted("months")
        longest = instalments[-1:] if instalments and texts["monthly"] else instalments.browse()
        quote_plans = []
        for plan in plans:
            order = plan.order_id
            insurer, name = self._plan_heading(order, plan)
            quote_plans.append({
                "insurer": insurer,
                "name": name,
                "highlight": plan.highlight,
                "sections": self._plain_sections(order, comparison),
                "total": order.amount_total,
                "levies": order.amount_tax + sum(
                    s["amount"] for s in order._client_sections() if s["key"] == "statutory_charges"),
                "monthly": longest.schedule(order.amount_total) if longest else False,
            })
        whatsapp = self._whatsapp(company)
        text = _("Hi %(company)s, I'd like to talk about my %(kind)s quote %(quote)s.",
                 company=company.name, kind=texts["kind"], quote=comparison.name)
        path = "%s/q/%s" % (self._odin_path(), comparison.id)
        return dict(texts, **{
            "comparison": comparison,
            "company": company,
            "status": self._status(comparison),
            "plans": quote_plans,
            "valid_until": comparison._odin_export_date(comparison.validity_date),
            "token": token,
            "pdf_url": "%s/pdf?token=%s" % (path, token),
            "callback_url": "%s/callback" % path,
            "change_url": "%s?quote_id=%s&quote=%s" % (self._odin_path(), comparison.id, token),
            "whatsapp_url": "https://wa.me/%s?text=%s" % (whatsapp, url_quote(text))
            if whatsapp else "",
            "contact_by": list(CONTACT_BY),
            "contact_when": list(CONTACT_WHEN),
            "reply": company.odin_online_reply_time or "",
            # Named to a client their agent quoted or sent the link to: anyone
            # else is told "an advisor", whoever ends up with it.
            "advisor": comparison.user_id if (
                comparison.odin_channel == "agent" or comparison.odin_online_via in ("agent", "client"))
            else comparison.user_id.browse(),
        })

    @api.model
    def _odin_page_template(self):
        return "odin_medical_quote_online.page"
