# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
import json

from psycopg2 import IntegrityError

from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from ..models.res_config_settings import PARAM_LEAD_ON_UNKNOWN, PARAM_MISSED_ACTIVITY, PARAM_TOKEN

TOKEN = "test-3cx-token"


@tagged("post_install", "-at_install")
class TestCrm3cx(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param(PARAM_TOKEN, TOKEN)
        icp.set_param(PARAM_MISSED_ACTIVITY, "True")
        icp.set_param(PARAM_LEAD_ON_UNKNOWN, "False")
        cls.env.company.country_id = cls.env.ref("base.ke")
        cls.agent = cls.env["res.users"].create({
            "name": "Jane Doe", "login": "jane@example.com", "email": "jane@example.com", "threecx_extension": "101",
        })
        cls.company = cls.env["res.partner"].create({
            "name": "Mavuno Traders", "is_company": True, "phone": "+254 20 1234567",
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Wanjiru Kamau",
            "parent_id": cls.company.id,
            "phone": "+254 712 345678",
            "email": "wanjiru@example.com",
        })
        cls.lead = cls.env["crm.lead"].create({
            "name": "Solar water heater quote",
            "contact_name": "Otieno Odhiambo",
            "partner_name": "Pwani Logistics",
            "phone": "0722 000111",
            "email_from": "otieno@example.com",
        })

    def _post(self, url, payload, key=TOKEN):
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["apikey"] = key
        res = self.url_open(url, data=json.dumps(payload), headers=headers)
        # The request wrote through the same transaction; drop what this
        # env cached before it (counts, activities, messages).
        self.env.invalidate_all()
        return res

    def _notes(self, record):
        return record.message_ids.filtered(lambda m: m.message_type == "comment")

    # ── lookup ──────────────────────────────────────────────────────────────

    def test_wrong_key_is_refused(self):
        self.assertEqual(self._post("/api/3cx/crm", {"number": "+254712345678"}, key="nope").status_code, 403)
        self.assertEqual(self._post("/api/3cx/crm", {"number": "+254712345678"}, key=None).status_code, 403)
        self.assertEqual(self._post("/api/3cx/call", {"number": "+254712345678"}, key="nope").status_code, 403)

    def test_lookup_contact_in_every_format_the_pbx_may_send(self):
        # E.164, national, no-plus international, spaced, and "match on last 9 digits".
        for number in ("+254712345678", "0712345678", "254712345678", "+254 712 345 678", "712345678"):
            res = self._post("/api/3cx/crm", {"number": number})
            self.assertEqual(res.status_code, 200, number)
            result = res.json()["result"]
            self.assertEqual(len(result), 1, number)
            record = result[0]
            self.assertEqual(record["partner_id"], str(self.partner.id), number)
            self.assertEqual((record["firstname"], record["lastname"]), ("Wanjiru", "Kamau"))
            self.assertEqual(record["name"], "Mavuno Traders", "a person's CompanyName is their employer")
            self.assertEqual(record["email"], "wanjiru@example.com")
            self.assertEqual(record["phone_e164"], "+254712345678", "what the 3CX client should dial")
            self.assertEqual(record["number"], number, "3CX only accepts a match that echoes the searched number")
            self.assertEqual(record["entity_type"], "Contacts")
            self.assertTrue(record["web_url"].endswith("/odoo/contacts/%d" % self.partner.id))

    def test_lookup_company(self):
        record = self._post("/api/3cx/crm", {"number": "+254201234567"}).json()["result"][0]
        self.assertEqual(record["partner_id"], str(self.company.id))
        self.assertEqual((record["firstname"], record["lastname"], record["name"]), ("", "", "Mavuno Traders"))

    def test_lookup_lead(self):
        result = self._post("/api/3cx/crm", {"number": "+254722000111"}).json()["result"]
        self.assertEqual(len(result), 1)
        record = result[0]
        self.assertEqual(record["partner_id"], "L%d" % self.lead.id)
        self.assertEqual(record["entity_type"], "Leads")
        self.assertEqual((record["firstname"], record["lastname"], record["name"]), ("Otieno", "Odhiambo", "Pwani Logistics"))
        self.assertEqual(record["contact_name"], "Solar water heater quote")
        self.assertIn("/odoo/action-", record["web_url"])
        self.assertTrue(record["web_url"].endswith("/%d" % self.lead.id))

    def test_lookup_unknown_short_or_empty(self):
        for payload in ({"number": "+254700000000"}, {"number": ""}, {"number": "101"}, {"number": "Anonymous"}, {}):
            self.assertEqual(self._post("/api/3cx/crm", payload).json(), {"result": [], "new_number": True}, payload)

    # ── search from the 3CX client ──────────────────────────────────────────

    def test_search_by_name_company_phone_email(self):
        ids = lambda field, text, **kw: [r["partner_id"] for r in self._post(
            "/api/3cx/search", dict(field=field, text=text, **kw)).json()["result"]]
        self.assertIn(str(self.partner.id), ids("name", "wanjiru"))
        self.assertIn("L%d" % self.lead.id, ids("name", "otieno"))
        self.assertIn("L%d" % self.lead.id, ids("name", "solar water"), "leads also match on their title")
        self.assertEqual(set(ids("company", "mavuno")), {str(self.company.id), str(self.partner.id)})
        self.assertIn("L%d" % self.lead.id, ids("company", "pwani"))
        self.assertEqual(ids("phone", "0712345678"), [str(self.partner.id)])
        self.assertEqual(ids("phone", "abc"), [], "alphabetic input must not error")
        self.assertEqual(ids("phone", "12"), [], "too short for phone_mobile_search, must not error")
        self.assertEqual(ids("email", "wanjiru@"), [str(self.partner.id)])
        self.assertEqual(ids("email", "WANJIRU@example.com", exact=True), [str(self.partner.id)])
        self.assertEqual(ids("email", "wanjiru@", exact=True), [], "LookupByEmail is an exact match")
        self.assertEqual(ids("email", "otieno@example.com", exact=True), ["L%d" % self.lead.id])
        self.assertEqual(ids("nonsense", "x"), [])
        self.assertEqual(ids("name", ""), [])

    # ── call journaling ─────────────────────────────────────────────────────

    def test_missed_call_logs_note_activity_and_call_record(self):
        res = self._post("/api/3cx/call", {
            "number": "+254712345678",
            "call_type": "Missed",
            "entity_id": str(self.partner.id),
            "entity_type": "Contacts",
            "agent": "101",
            "agent_first_name": "Jane",
            "agent_last_name": "Doe",
            "queue": "800",
            "duration": "00:00:00",
            "started_at": "2026-09-19T08:30:00Z",
            "subject": "3CX call",
            "body": "19/09/2026 11:30: Missed call from +254712345678 Wanjiru Kamau to Jane Doe (101)",
            "recording_url": "[RecordingUrl]",
            "summary": "[Summary]",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual((data["logged"], data["model"], data["id"], data["created"]), (True, "res.partner", self.partner.id, False))
        note = self._notes(self.partner)[:1]
        self.assertIn("3CX call", note.body)
        self.assertIn("Missed call from +254712345678 Wanjiru Kamau to Jane Doe (101)", note.body)
        self.assertNotIn("Recording", note.body, "unresolved 3CX placeholders are dropped")
        self.assertEqual(note.author_id, self.agent.partner_id, "matched on the extension")
        activity = self.partner.activity_ids
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity.id, data["activity_id"])
        self.assertEqual(activity.activity_type_id, self.env.ref("mail.mail_activity_data_call"))
        self.assertEqual(activity.summary, "Call back +254712345678")
        self.assertEqual(activity.user_id, self.agent, "no salesperson on the contact, so the agent who missed it")
        call = self.env["crm.3cx.call"].browse(data["call_id"])
        self.assertEqual((call.call_type, call.direction, call.answered), ("missed", "inbound", False))
        self.assertEqual((call.partner_id, call.lead_id.id, call.user_id, call.extension, call.queue), (self.partner, False, self.agent, "101", "800"))
        self.assertEqual(str(call.started_at), "2026-09-19 08:30:00")
        self.assertEqual((call.duration, call.duration_display), (0, "0:00:00"))
        self.assertEqual(call.message_id, note)
        self.assertEqual(call.activity_id, activity)
        self.assertFalse(call.recording_url)
        self.assertEqual(self.partner.threecx_call_count, 1)

        # A second miss does not pile up call-backs.
        self._post("/api/3cx/call", {"number": "+254712345678", "call_type": "Missed", "entity_id": str(self.partner.id)})
        self.assertEqual(len(self.partner.activity_ids), 1)
        self.assertEqual(self.partner.threecx_call_count, 2)

        # The next answered call closes the call-back.
        res = self._post("/api/3cx/call", {
            "number": "+254712345678", "call_type": "Inbound", "entity_id": str(self.partner.id),
            "agent_email": "Jane@Example.com", "duration": "00:03:12", "recording_url": "https://pbx.example.com/rec/1.wav",
            "summary": "Customer asked for a quote.",
        })
        self.assertFalse(self.partner.activity_ids, "reached, so the call-back is done")
        call = self.env["crm.3cx.call"].browse(res.json()["call_id"])
        self.assertEqual((call.call_type, call.answered, call.duration, call.duration_display), ("inbound", True, 192, "0:03:12"))
        self.assertEqual(call.user_id, self.agent, "matched on the email when no extension is sent")
        self.assertEqual(call.recording_url, "https://pbx.example.com/rec/1.wav")
        note = self._notes(self.partner)[:1]
        self.assertIn('href="https://pbx.example.com/rec/1.wav"', note.body)
        self.assertIn("Customer asked for a quote.", note.body)

    def test_call_without_rendered_body_gets_a_default_text(self):
        res = self._post("/api/3cx/call", {
            "number": "0722000111", "call_type": "Outbound", "entity_id": "L%d" % self.lead.id,
            "agent": "202", "agent_first_name": "Ali", "duration": "00:01:05", "started_at": "2026-09-19T09:00:00Z",
        })
        self.assertEqual(res.json()["model"], "crm.lead")
        note = self._notes(self.lead)[:1]
        self.assertIn("Outbound call to 0722000111", note.body)
        self.assertIn("with Ali (ext. 202)", note.body)
        self.assertIn("duration 00:01:05", note.body)
        self.assertIn("started 2026-09-19 09:00 UTC", note.body)
        self.assertEqual(note.author_id, self.env.ref("base.partner_root"), "unknown agent, so OdooBot")
        self.assertFalse(self.lead.activity_ids)
        call = self.env["crm.3cx.call"].search([("lead_id", "=", self.lead.id)])
        self.assertEqual((len(call), call.direction, call.duration), (1, "outbound", 65))
        self.assertEqual(self.lead.threecx_call_count, 1)

    def test_call_matches_by_number_when_3cx_sends_no_entity(self):
        res = self._post("/api/3cx/call", {"number": "0712345678", "call_type": "Inbound", "duration": "00:00:10"})
        self.assertEqual((res.json()["model"], res.json()["id"]), ("res.partner", self.partner.id))

    def test_unknown_caller_is_ignored_unless_enabled(self):
        payload = {"number": "+254733999999", "call_type": "Missed", "agent": "101"}
        self.assertEqual(self._post("/api/3cx/call", payload).json()["logged"], False)
        self.assertFalse(self.env["crm.3cx.call"].search([("number", "=", "+254733999999")]))

        self.env["ir.config_parameter"].sudo().set_param(PARAM_LEAD_ON_UNKNOWN, "True")
        outbound = self._post("/api/3cx/call", {"number": "+254733999999", "call_type": "Notanswered"}).json()
        self.assertEqual(outbound["logged"], False, "only inbound calls create leads")

        data = self._post("/api/3cx/call", payload).json()
        self.assertTrue(data["logged"] and data["created"])
        lead = self.env["crm.lead"].browse(data["id"])
        self.assertEqual((lead.name, lead.phone, lead.user_id), ("Call from +254733999999", "+254733999999", self.agent))
        self.assertEqual(len(lead.activity_ids), 1)
        self.assertEqual(self.env["crm.3cx.call"].browse(data["call_id"]).lead_id, lead)

    # ── chat journaling ─────────────────────────────────────────────────────

    def test_chat_is_logged_on_the_contact(self):
        res = self._post("/api/3cx/chat", {
            "email": "wanjiru@example.com", "entity_id": "", "agent": "101", "subject": "3CX chat",
            "messages": "[10:01] Wanjiru: Hi, do you deliver to Kisumu?\n[10:02] Jane: Yes, next day.",
            "started_at": "2026-09-19T10:00:00Z",
        })
        self.assertEqual((res.json()["logged"], res.json()["model"], res.json()["id"]), (True, "res.partner", self.partner.id))
        note = self._notes(self.partner)[:1]
        self.assertIn("3CX chat", note.body)
        self.assertIn("do you deliver to Kisumu?", note.body)
        self.assertIn("<br>", str(note.body).replace("<br/>", "<br>"), "line breaks survive")
        self.assertFalse(self.env["crm.3cx.call"].search([("partner_id", "=", self.partner.id)]), "chats are not calls")

    def test_chat_from_unknown_visitor_creates_lead_when_enabled(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM_LEAD_ON_UNKNOWN, "True")
        data = self._post("/api/3cx/chat", {"email": "visitor@example.com", "messages": "Hello?"}).json()
        self.assertTrue(data["created"])
        lead = self.env["crm.lead"].browse(data["id"])
        self.assertEqual((lead.name, lead.email_from), ("Chat from visitor@example.com", "visitor@example.com"))

    # ── create contact from the client ──────────────────────────────────────

    def test_create_contact_from_client(self):
        res = self._post("/api/3cx/contact", {
            "firstname": "Amina", "lastname": "Hassan", "number": "+254711222333",
            "email": "amina@example.com", "company": "Kisumu Fresh",
        })
        result = res.json()["result"]
        self.assertEqual(len(result), 1)
        record = result[0]
        self.assertTrue(record["partner_id"].startswith("L"))
        lead = self.env["crm.lead"].browse(int(record["partner_id"][1:]))
        self.assertEqual((lead.contact_name, lead.partner_name, lead.phone, lead.email_from),
                         ("Amina Hassan", "Kisumu Fresh", "+254711222333", "amina@example.com"))
        self.assertEqual((record["firstname"], record["lastname"], record["name"]), ("Amina", "Hassan", "Kisumu Fresh"))
        self.assertEqual(record["number"], "+254711222333")
        self.assertEqual(self._post("/api/3cx/contact", {}).status_code, 400)

    # ── template download ───────────────────────────────────────────────────

    def test_template_download_is_admin_only_and_personalised(self):
        anonymous = self.url_open("/crm_3cx/template")
        self.assertIn("/web/login", anonymous.url, "anonymous users are sent to the login page")
        self.authenticate("admin", "admin")
        res = self.url_open("/crm_3cx/template")
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/xml", res.headers["Content-Type"])
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        self.assertIn('Url="[hostodoo]/api/3cx/crm"', res.text)
        self.assertIn('Name="hostodoo"', res.text)
        self.assertIn('Default="%s"' % base, res.text)
        for scenario in ("LookupByEmail", "SearchContacts_FullName", "ReportCall", "ReportChat", "CreateContactRecordFromClient"):
            self.assertIn('Scenario Id="%s"' % scenario, res.text)

    @mute_logger("odoo.sql_db")
    def test_extension_is_unique_per_user(self):
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env["res.users"].create({"name": "Dup", "login": "dup@example.com", "threecx_extension": "101"})
