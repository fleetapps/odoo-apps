# Copyright (C) Persevida S.L. / FL1 sro, the original `3cxcrm` module (Odoo 14.0 to 18.0).
# Copyright (C) 2026 Odin (fleetapps), the Odoo 19 port and everything beyond lookup.
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
"""Endpoints called by the 3CX server-side CRM engine.

The 3CX side is upload_on_3cx_pbx/3cx_odoo.xml: one scenario per endpoint.
Every request is a JSON POST carrying the shared key in an `apikey` header.

    /api/3cx/crm      lookup by number, runs when a call rings
    /api/3cx/search   contact search from the 3CX Web Client, and lookup by email
    /api/3cx/call     ReportCall: note in the chatter, call-back activity on a miss
    /api/3cx/chat     ReportChat: the conversation in the chatter
    /api/3cx/contact  CreateContactRecordFromClient: a lead from the 3CX client

Records go back as {"result": [record, ...]} so the template's `result.*` paths
resolve per record (3CX, "CRM Template XML description", Rules). The record
keys of the upstream module are kept so its template still works for lookup.

Facts about the 3CX engine this code relies on, from 3CX's template rules:
  * `[Number]` in a POST body is the caller number after the PBX phonebook
    "match on last N digits" setting is applied, with no + or 00 prefix, so
    it may be a suffix of what Odoo stores.
  * A lookup result is discarded unless one returned phone output contains
    the searched number; the number is therefore echoed back as `number`.
  * Booleans in responses are read by 3CX as the strings "True"/"False".
"""
import hmac
import logging
import re
from datetime import timedelta

import phonenumbers
from markupsafe import Markup, escape
from werkzeug.exceptions import BadRequest, Forbidden

from odoo import fields, http
from odoo.http import content_disposition, request
from odoo.tools import file_open

from ..models.res_config_settings import PARAM_LEAD_ON_UNKNOWN, PARAM_POP_OPPORTUNITY, PARAM_TOKEN, param_flag

_logger = logging.getLogger(__name__)

# Fewer digits than this is an extension or a short code, and a substring search
# on it would match half the address book. 3CX's own minimum for "match on last
# N digits" is well above it.
MIN_SEARCH_DIGITS = 5
LOOKUP_LIMIT = 10
SEARCH_LIMIT = 20  # what 3CX asks templates to request per search
RENEWAL_HINT_DAYS = 60  # a renewal this close is worth a word in the ring-time popup
CALL_LABELS = {
    "inbound": "Inbound call",
    "outbound": "Outbound call",
    "missed": "Missed call",
    "notanswered": "Outbound call, not answered",
}
# How each 3CX search scenario maps onto the two models. `exact` is used by
# LookupByEmail, where 3CX wants the contact for one address, not a list.
SEARCH_DOMAINS = {
    "name": {
        "res.partner": lambda t, exact: [("complete_name", "ilike", t)],
        "crm.lead": lambda t, exact: ["|", ("contact_name", "ilike", t), ("name", "ilike", t)],
    },
    "company": {
        "res.partner": lambda t, exact: [("commercial_company_name", "ilike", t)],
        "crm.lead": lambda t, exact: [("partner_name", "ilike", t)],
    },
    "phone": None,  # built from _candidates(), see _search
    "email": {
        "res.partner": lambda t, exact: [("email", "=ilike" if exact else "ilike", t)],
        "crm.lead": lambda t, exact: [("email_from", "=ilike" if exact else "ilike", t)],
    },
}
TEMPLATE_FILE = "crm_3cx/upload_on_3cx_pbx/3cx_odoo.xml"
TEMPLATE_HOST_PARAM = re.compile(r'(<Parameter Name="hostodoo"[^>]*Default=")("[^>]*/>)')


def _text(value):
    return str(value).strip() if value not in (None, False) else ""


def _truthy(value):
    return value is True or _text(value).lower() in ("true", "1", "yes")


def _multiline(text):
    """Escaped HTML with line breaks preserved.

    3CX renders the administrator's journaling texts with `[LineBreak]`, which
    reaches us as a newline, and their guidance for HTML targets is a literal
    `<br/>`; both become a break, and nothing else from the PBX is HTML.
    """
    text = re.sub(r"(?i)<br\s*/?>", "\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    return Markup("<br/>").join(escape(line) for line in text.split("\n"))


def _resolved(value):
    """A 3CX variable that does not exist on this PBX version comes through as
    its own placeholder, e.g. "[Transcription]" on a system without AI."""
    value = _text(value)
    return "" if re.fullmatch(r"\[\w+\]", value) else value


class Crm3cxController(http.Controller):

    # ── plumbing ────────────────────────────────────────────────────────────

    @staticmethod
    def _authenticate():
        expected = request.env["ir.config_parameter"].sudo().get_param(PARAM_TOKEN) or ""
        given = request.httprequest.headers.get("apikey") or ""
        if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
            _logger.warning("3CX request refused: bad or missing apikey (from %s)", request.httprequest.remote_addr)
            raise Forbidden("Wrong or missing apikey")

    @staticmethod
    def _payload():
        try:
            data = request.get_json_data()
        except ValueError:
            raise BadRequest("Body must be JSON")
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _json(payload, status=200):
        return request.make_json_response(payload, status=status)

    @staticmethod
    def _field(record, name):
        """A field that exists on some Odoo versions only: `mobile` left
        res.partner and crm.lead in 19.0, `phone_sanitized` is 17.0+."""
        return (record[name] or "") if name in record._fields else ""

    @staticmethod
    def _split_name(name):
        parts = _text(name).split(" ", 1)
        return parts[0], (parts[1] if len(parts) > 1 else "")

    @staticmethod
    def _xmlid_id(xmlid):
        return request.env["ir.model.data"].sudo()._xmlid_to_res_id(xmlid)

    # ── matching ────────────────────────────────────────────────────────────

    @staticmethod
    def _candidates(number):
        """Search terms for one caller number, most specific first.

        A trunk may deliver +254712345678, 0712345678 or 254712345678 for the
        same subscriber, the PBX may have cut it to its last N digits, and
        Odoo stores whatever the user typed. The E.164 form matches records
        stored international (phone_mobile_search anchors a leading + or 00);
        the raw term and the national significant number match the rest by
        substring.
        """
        number = _text(number)
        terms = [number]
        country = request.env.company.country_id.code or None
        try:
            parsed = phonenumbers.parse(number, country)
            if phonenumbers.is_possible_number(parsed):
                terms.append(phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164))
                terms.append(str(parsed.national_number))
        except phonenumbers.NumberParseException:
            pass
        seen, out = set(), []
        for term in terms:
            if term in seen or len(re.sub(r"\D", "", term)) < MIN_SEARCH_DIGITS:
                continue
            seen.add(term)
            out.append(term)
        return out

    @staticmethod
    def _ranked(records):
        """People before the companies they belong to, so the popup names the
        person; otherwise the model's own order (leads: priority, newest)."""
        if records._name == "res.partner":
            return records.sorted(key=lambda p: (p.is_company, p.id))
        return records

    def _find(self, number):
        """Contacts first, then leads and opportunities, for the first term
        that matches anything. Never both: 3CX treats the list as one entity."""
        terms = self._candidates(number)
        for model in ("res.partner", "crm.lead"):
            Model = request.env[model].sudo()
            for term in terms:
                found = Model.search([("phone_mobile_search", "ilike", term)], limit=LOOKUP_LIMIT)
                if found:
                    return self._ranked(found)
        return request.env["res.partner"]

    def _ring(self, number):
        """What 3CX shows while the phone rings: the records for the number,
        and, when a known contact has an open deal, that deal in front."""
        records = self._find(number)
        if records and records._name == "res.partner" and param_flag(request.env, PARAM_POP_OPPORTUNITY):
            for partner in records:
                deal = partner._threecx_open_opportunity()
                if deal:
                    return [(deal, partner)] + [(r, None) for r in records if r != partner]
        return [(r, None) for r in records]

    def _by_email(self, email):
        email = _text(email)
        if not email:
            return request.env["res.partner"]
        for model, field in (("res.partner", "email"), ("crm.lead", "email_from")):
            found = request.env[model].sudo().search([(field, "=ilike", email)], limit=LOOKUP_LIMIT)
            if found:
                return self._ranked(found)
        return request.env["res.partner"]

    def _phone_domain(self, text):
        """Every form of the number, OR-ed: what _find tries one by one."""
        terms = self._candidates(text)
        if not terms:
            return None
        return ["|"] * (len(terms) - 1) + [("phone_mobile_search", "ilike", t) for t in terms]

    def _search(self, field, text, exact=False):
        text = _text(text)
        if field not in SEARCH_DOMAINS or not text:
            return []
        if field == "phone":
            domain = self._phone_domain(text)
            if domain is None:
                return []  # letters or too few digits: nothing to match, and no error
            domains = {"res.partner": lambda t, e: domain, "crm.lead": lambda t, e: domain}
        else:
            domains = SEARCH_DOMAINS[field]
        records = []
        for model in ("res.partner", "crm.lead"):
            Model = request.env[model].sudo()
            found = Model.search(domains[model](text, exact), limit=SEARCH_LIMIT - len(records))
            records.extend(self._ranked(found))
            if len(records) >= SEARCH_LIMIT:
                break
        return records

    @staticmethod
    def _entity(entity_id):
        """The record 3CX matched at lookup time: "L<id>" is a lead, "<id>" a contact."""
        entity_id = _text(entity_id)
        model = "res.partner"
        if entity_id[:1] in ("L", "l"):
            model, entity_id = "crm.lead", entity_id[1:]
        if not entity_id.isdigit():
            return None
        return request.env[model].sudo().browse(int(entity_id)).exists() or None

    # ── what 3CX gets back ──────────────────────────────────────────────────

    @staticmethod
    def _context_suffix(partner, deal=None):
        """What the agent should know before saying hello, in the one line 3CX
        shows under the caller's name."""
        parts = []
        if deal:
            parts.append("%s (%s)" % (deal.name, deal.stage_id.name or deal.type))
        if partner and partner.threecx_renewal_date:
            today = fields.Date.context_today(partner)
            if today <= partner.threecx_renewal_date <= today + timedelta(days=RENEWAL_HINT_DAYS):
                parts.append("Renewal %s" % partner.threecx_renewal_date.strftime("%d %b"))
        return parts

    def _record_payload(self, record, number="", caller=None):
        """`caller` is the contact whose number matched when `record` is their
        open opportunity: the popup names the person, the link opens the deal."""
        base = record.get_base_url()
        if caller is not None:
            payload = self._record_payload(caller, number)
            company = caller.name if caller.is_company else (caller.commercial_company_name or "")
            payload.update({
                "partner_id": "L%d" % record.id,
                "entity_type": "Leads",
                "type": record.type,
                "contact_name": record.name,
                "web_url": self._lead_url(record, base),
                "name": " · ".join(p for p in [company] + self._context_suffix(caller, record) if p),
            })
            return payload
        if record._name == "res.partner":
            if "firstname" in record._fields:  # OCA partner_firstname
                firstname, lastname = record.firstname or "", record.lastname or ""
            else:
                firstname, lastname = self._split_name(record.name)
            if record.is_company:
                firstname, lastname = "", ""
            company = record.name if record.is_company else (record.commercial_company_name or "")
            company = " · ".join(p for p in [company] + self._context_suffix(record) if p)
            payload = {
                "partner_id": str(record.id),
                "entity_type": "Contacts",
                "type": record.type,
                "contact_name": "",
                "email": record.email or "",
                "company_type": record.company_type if record.company_type == "company" else "",
                "web_url": "%s/odoo/contacts/%d" % (base, record.id),
            }
        else:
            firstname, lastname = self._split_name(record.contact_name)
            company = record.partner_name or record.partner_id.commercial_company_name or ""
            if not (firstname or company):
                company = record.name  # 3CX drops a record with no name at all
            payload = {
                "partner_id": "L%d" % record.id,
                "entity_type": "Leads",
                "type": record.type,
                "contact_name": record.name,
                "email": record.email_from or "",
                "company_type": "",
                "web_url": self._lead_url(record, base),
            }
        phone = record.phone or ""
        payload.update({
            "firstname": firstname,
            "lastname": lastname,
            "name": company,
            "display_name": record.display_name,
            "phone": phone,
            # E.164 when Odoo could compute it: what the 3CX client should dial.
            "phone_e164": self._field(record, "phone_sanitized") or phone,
            "mobile": self._field(record, "mobile"),
            "number": number,
        })
        payload.update(self._routing(record))
        return payload

    @staticmethod
    def _routing(record):
        """For 3CX Call Flow Designer, which gets this JSON raw through the
        LookupFromCFD scenario: enough to route a caller to the salesperson who
        owns their deal, or to a retention queue when a renewal is close."""
        partner = record if record._name == "res.partner" else record.partner_id
        owner = record.user_id
        deal = record if record._name == "crm.lead" else (partner._threecx_open_opportunity() if partner else None)
        if deal and not owner:
            owner = deal.user_id
        if deal:
            status = "opportunity" if deal.type == "opportunity" else "lead"
        elif partner and partner.customer_rank:
            status = "customer"
        else:
            status = "contact"
        renewal = partner.threecx_renewal_date if partner else False
        return {
            "owner_extension": owner.threecx_extension or "",
            "owner_name": owner.name or "",
            "customer_status": status,
            "stage": deal.stage_id.name if deal else "",
            "renewal_in_days": (renewal - fields.Date.context_today(record)).days if renewal else "",
        }

    def _lead_url(self, lead, base):
        action = "crm.crm_lead_action_pipeline" if lead.type == "opportunity" else "crm.crm_lead_all_leads"
        return "%s/odoo/action-%d/%d" % (base, self._xmlid_id(action), lead.id)

    def _records_response(self, records, number=""):
        """`records` is a recordset, a list of records, or a list of (record, caller) pairs."""
        pairs = [r if isinstance(r, tuple) else (r, None) for r in records]
        return self._json({"result": [self._record_payload(r, number, caller) for r, caller in pairs]})

    # ── journaling ──────────────────────────────────────────────────────────

    @staticmethod
    def _agent_user(data):
        """The Odoo user behind the 3CX extension: the one who claimed that
        extension on their user form, else the one whose login or email is
        the address 3CX has for the agent."""
        Users = request.env["res.users"].sudo()
        ext = _text(data.get("agent"))
        if ext:
            user = Users.search([("threecx_extension", "=", ext), ("share", "=", False)], limit=1)
            if user:
                return user
        email = _text(data.get("agent_email")).lower()
        if not email:
            return Users
        return Users.search(
            ["&", ("share", "=", False), "|", ("login", "=ilike", email), ("email", "=ilike", email)], limit=1,
        )

    @staticmethod
    def _agent_label(data):
        name = " ".join(p for p in (_text(data.get("agent_first_name")), _text(data.get("agent_last_name"))) if p)
        ext = _text(data.get("agent"))
        if name and ext:
            return "%s (ext. %s)" % (name, ext)
        return name or (ext and "ext. %s" % ext) or ""

    def _default_call_text(self, data, call_type, number):
        """Used when the template did not send a rendered body, e.g. the
        upstream module's template or a hand-made one."""
        details = []
        agent = self._agent_label(data)
        if agent:
            details.append(("with " if call_type in ("inbound", "outbound") else "agent ") + agent)
        if _text(data.get("duration")) and call_type in ("inbound", "outbound"):
            details.append("duration %s" % _text(data["duration"]))
        if _text(data.get("queue")):
            details.append("via queue %s" % _text(data["queue"]))
        started = _text(data.get("started_at"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", started):  # the template's format
            started = started[:16].replace("T", " ") + " UTC"
        if started:
            details.append("started %s" % started)
        preposition = "to" if call_type in ("outbound", "notanswered") else "from"
        return "%s %s %s\n%s" % (CALL_LABELS.get(call_type, "Call"), preposition, number or "an unknown number", " · ".join(details))

    def _note(self, data, kind, call_type, number):
        subject = _text(data.get("subject"))
        if kind == "call":
            text = _text(data.get("body")) or self._default_call_text(data, call_type, number)
            subject = subject or CALL_LABELS.get(call_type, "Call")
        else:
            text = _resolved(data.get("messages"))
            subject = subject or "3CX chat"
        # Markup % escapes every operand, so nothing from the PBX is ever HTML.
        html = Markup("<p><b>%s</b></p>") % subject
        if text:
            html += Markup("<p>%s</p>") % _multiline(text)
        extras = []
        recording = _resolved(data.get("recording_url"))
        if recording.startswith(("http://", "https://")):
            extras.append(Markup('<a href="%s" target="_blank" rel="noopener">Call recording</a>') % recording)
        for key, label in (("summary", "Summary"), ("sentiment", "Sentiment"), ("transcription", "Transcription")):
            value = _resolved(data.get(key))
            if value:
                extras.append(Markup("<b>%s:</b> %s") % (label, _multiline(value)))
        if extras:
            html += Markup("<p>%s</p>") % Markup("<br/>").join(extras)
        return html

    def _create_lead(self, name, number="", email="", company="", user=None, kind=False):
        """A lead owned by `user` (the agent, when 3CX told us who) or by the
        leader of the default sales team; never by the public user this
        request runs as, which is what crm.lead's default would pick. Created
        as that user too, so the first-call activity is not stamped "Public user"."""
        actor = user or request.env.ref("base.user_admin")
        Lead = request.env["crm.lead"].with_user(actor).sudo()
        team = request.env["crm.team"].sudo()._get_default_team_id(user_id=user.id if user else False)
        owner = user or team.user_id
        return Lead.create({
            "name": name,
            "type": "lead" if Lead._threecx_leads_enabled() else "opportunity",
            "phone": number or False,
            "email_from": email or False,
            "partner_name": company or False,
            "contact_name": name if company else False,
            "user_id": owner.id if owner else False,
            "team_id": team.id if team else False,
            "threecx_kind": kind,
        })

    def _journal(self, data, kind):
        """Shared by ReportCall and ReportChat: find (or create) the record,
        post the note, and keep the call-back activity in step."""
        number = _text(data.get("number"))
        email = _text(data.get("email"))
        call_type = _text(data.get("call_type")).lower() if kind == "call" else "inbound"

        record = self._entity(data.get("entity_id"))
        if record is None and number:
            record = self._find(number)[:1] or None
        if record is None and email:
            record = self._by_email(email)[:1] or None

        agent = self._agent_user(data)
        created = False
        if record is None:
            inbound = call_type in ("inbound", "missed")
            if not (param_flag(request.env, PARAM_LEAD_ON_UNKNOWN, False) and inbound and (number or email)):
                return self._json({"logged": False, "reason": "no matching contact or lead"})
            what = "Call" if kind == "call" else "Chat"
            record = self._create_lead("%s from %s" % (what, number or email), number=number, email=email,
                                       user=agent or None, kind="inbound" if kind == "call" else "chat")
            created = True

        # Activities and their "done" messages are stamped with env.user, so
        # act as the agent (else the administrator) rather than the public user.
        actor = agent or request.env.ref("base.user_admin")
        record = record.with_user(actor).sudo()
        author = agent.partner_id if agent else request.env.ref("base.partner_root")
        message = record.message_post(
            body=self._note(data, kind, call_type, number),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            author_id=author.id,
        )

        result = {"logged": True, "model": record._name, "id": record.id, "created": created}
        if kind == "call":
            call = self._log_call(data, call_type, number, record, agent, message)
            # Call-backs, retry cadence, unreachable, stage, SLA: the rules live
            # on the record's model, so the same call has the same effect
            # whether it came from 3CX or from a test.
            activity = record._threecx_after_call(call, agent or None)
            if activity:
                call.activity_id = activity
            result.update(call_id=call.id, activity_id=activity.id if activity else False)
        return self._json(result)

    def _log_call(self, data, call_type, number, record, agent, message):
        """The row behind CRM > Reporting > 3CX Calls."""
        Call = request.env["crm.3cx.call"].sudo()
        is_lead = record._name == "crm.lead"
        return Call.create({
            "call_type": call_type if call_type in ("inbound", "missed", "outbound", "notanswered") else "inbound",
            "number": number,
            "partner_id": (record.partner_id.id if is_lead else record.id) or False,
            "lead_id": record.id if is_lead else False,
            "user_id": agent.id if agent else False,
            "extension": _text(data.get("agent")),
            "queue": _text(data.get("queue")),
            # A template without CallStartTimeUTC still reports the call now.
            "started_at": Call._parse_started_at(_text(data.get("started_at"))) or fields.Datetime.now(),
            "duration": Call._parse_duration(_text(data.get("duration"))),
            "recording_url": _resolved(data.get("recording_url")),
            "summary": _resolved(data.get("summary")),
            "sentiment": _resolved(data.get("sentiment")),
            "transcription": _resolved(data.get("transcription")),
            "message_id": message.id if message else False,
            "company_id": (record.company_id.id if "company_id" in record._fields and record.company_id else request.env.company.id),
        })

    # ── routes ──────────────────────────────────────────────────────────────

    @http.route("/api/3cx/crm", type="http", auth="public", methods=["POST"], csrf=False, readonly=True)
    def lookup(self, **kw):
        """Contact lookup by number: the scenario with the empty Id."""
        self._authenticate()
        number = _text(self._payload().get("number"))
        pairs = self._ring(number)
        if not pairs:
            return self._json({"result": [], "new_number": True})
        return self._records_response(pairs, number)

    @http.route("/api/3cx/search", type="http", auth="public", methods=["POST"], csrf=False, readonly=True)
    def search(self, **kw):
        """SearchContacts_* (free text from the 3CX client) and LookupByEmail."""
        self._authenticate()
        data = self._payload()
        return self._records_response(self._search(_text(data.get("field")), data.get("text"), _truthy(data.get("exact"))))

    @http.route("/api/3cx/call", type="http", auth="public", methods=["POST"], csrf=False)
    def report_call(self, **kw):
        self._authenticate()
        return self._journal(self._payload(), "call")

    @http.route("/api/3cx/chat", type="http", auth="public", methods=["POST"], csrf=False)
    def report_chat(self, **kw):
        self._authenticate()
        return self._journal(self._payload(), "chat")

    @http.route("/api/3cx/contact", type="http", auth="public", methods=["POST"], csrf=False)
    def create_contact(self, **kw):
        """An agent filled in "create contact" in the 3CX client: make a lead,
        and return it the way a lookup would so the client can show it."""
        self._authenticate()
        data = self._payload()
        number, email, company = _text(data.get("number")), _text(data.get("email")), _text(data.get("company"))
        person = " ".join(p for p in (_text(data.get("firstname")), _text(data.get("lastname"))) if p)
        if not (person or company or number or email):
            raise BadRequest("Nothing to create")
        lead = self._create_lead(person or company or number or email, number=number, email=email, company=company)
        return self._records_response(lead, number)

    @http.route("/crm_3cx/template", type="http", auth="user", methods=["GET"])
    def template(self, **kw):
        """The 3CX template with this database's URL filled in."""
        if not request.env.user.has_group("base.group_system"):
            raise Forbidden()
        base = request.env["ir.config_parameter"].sudo().get_param("web.base.url") or request.httprequest.url_root
        with file_open(TEMPLATE_FILE) as f:
            xml = f.read()
        xml = TEMPLATE_HOST_PARAM.sub(lambda m: m.group(1) + base.rstrip("/") + m.group(2), xml, count=1)
        return request.make_response(xml, headers=[
            ("Content-Type", "application/xml; charset=utf-8"),
            ("Content-Disposition", content_disposition("3cx_odoo.xml")),
        ])
