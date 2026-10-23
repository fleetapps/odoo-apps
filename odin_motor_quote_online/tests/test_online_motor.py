from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged
from odoo.tools import html2plaintext

from odoo.addons.odin_motor_quote.tests.test_motor import MotorCase


class OnlineMotorCase(MotorCase):
    """odin_motor_quote's test rate cards, priced by a stranger."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Online = cls.env["odin.motor.quote.online"]
        cls.Comparison = cls.env["odin.quote.comparison"]
        cls.company.odin_online_user_id = False

    def vehicle(self, **values):
        return dict({"class_id": self.private.id, "cover": "comprehensive", "value": 800000,
                     "year": self.year - 5}, **values)

    def contact(self, **values):
        return dict({"name": "Peter Kamau", "phone": "0722 555 666", "email": "",
                     "contact_by": "call", "consent": True}, **values)

    def plans(self, **values):
        return {p["id"]: p for p in self.Online.options(self.vehicle(**values))["plans"]}


@tagged("post_install", "-at_install")
class TestOnlineMotor(OnlineMotorCase):

    def test_vehicle_types_in_plain_words(self):
        classes = {c["id"]: c for c in self.Online.page_props()["classes"]}
        self.assertEqual(set(classes), {self.private.id, self.psv.id}, "only types with a rate")
        self.assertEqual(classes[self.psv.id]["name"], "Taxi, Uber or matatu")
        self.assertEqual(classes[self.psv.id]["icon"], "fa-taxi")
        self.assertEqual([c["key"] for c in classes[self.private.id]["covers"]], ["comprehensive"])
        self.private.odin_online = False
        self.assertNotIn(self.private.id, [c["id"] for c in self.Online.page_props()["classes"]])
        with self.assertRaisesRegex(UserError, "what you drive"):
            self.Online.options(self.vehicle())

    def test_prices_with_what_is_included(self):
        plans = self.plans()
        self.assertEqual(list(plans), [self.definite_private.id, self.fidelity_low.id], "cheapest first")
        definite = plans[self.definite_private.id]
        self.assertEqual(definite["insurer"], "Test Definite")
        self.assertEqual(definite["name"], "Full cover (comprehensive)")
        self.assertEqual(definite["basic"], 37500)
        self.assertEqual(definite["included"], ["Riots, strikes & terrorism"])
        self.assertEqual(definite["per"], "a year")
        extras = {e["key"]: e for e in definite["extras"]}
        self.assertEqual(set(extras), {"excess", "courtesy"})
        self.assertEqual([o["label"] for o in extras["courtesy"]["options"]], ["10 days", "20 days"])
        self.assertAlmostEqual(definite["total"], 37500 * 1.0045 + 40, delta=0.02,
                               msg="levies and stamp duty in the price")

    def test_extras_change_the_price(self):
        rate = self.definite_private
        plain = self.plans()[rate.id]
        extras = {str(rate.id): ["excess", "courtesy20"]}
        plan = {p["id"]: p for p in self.Online.options(self.vehicle(), extras)["plans"]}[rate.id]
        chosen = {e["key"]: e["chosen"] for e in plan["extras"]}
        self.assertEqual(chosen, {"excess": "excess", "courtesy": "courtesy20"})
        self.assertAlmostEqual(plan["premium"] - plain["premium"], 3000 + 6000)
        forged = {str(rate.id): ["nonsense", "pvt"]}
        plan = {p["id"]: p for p in self.Online.options(self.vehicle(), forged)["plans"]}[rate.id]
        self.assertEqual(plan["premium"], plain["premium"], "only what the insurer offers")

    def test_nobody_can_quote_it(self):
        result = self.Online.options(self.vehicle(year=self.year - 20))
        self.assertFalse(result["plans"])
        self.assertEqual(result["refused"], "Vehicles up to 15 years old.")

    def test_checks(self):
        for values, message in (
                ({"cover": "tpo"}, "full cover or third party"),
                ({"value": 0}, "worth today"),
                ({"year": 1900}, "year it was made"),
                ({"class_id": self.psv.id, "cover": "tpo", "value": 0}, "passengers")):
            with self.assertRaisesRegex(UserError, message):
                self.Online.options(self.vehicle(**values))

    def test_psv_third_party(self):
        plans = self.Online.options({"class_id": self.psv.id, "cover": "tpo", "seats": 14,
                                     "year": self.year - 3})["plans"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["name"], "Third party only")
        self.assertEqual(plans[0]["basic"], 10000)
        self.assertEqual(plans[0]["included"], ["Passenger cover for 14 passengers"])
        self.assertFalse(plans[0]["extras"])

    def test_submit(self):
        result = self.Online.submit(
            self.vehicle(registration="kda 123a", make_model="Toyota Fielder"),
            {str(self.definite_private.id): ["excess"]},
            [self.definite_private.id, self.fidelity_low.id], self.contact(), {})
        comparison = self.Comparison.search([("odin_channel", "=", "online")], order="id desc", limit=1)
        self.assertIn("/motor-quote/q/%s?token=" % comparison.id, result["url"])
        self.assertEqual(comparison.odin_quote_type, "motor")
        self.assertEqual(comparison.title, "Motor Insurance")
        orders = comparison.plan_ids.sorted("sequence").order_id
        self.assertEqual(orders.odin_motor_rate_id, self.definite_private | self.fidelity_low)
        self.assertTrue(orders[0].order_line.filtered(
            lambda l: l.product_id == self.env.ref("odin_motor_quote.product_motor_excess")))
        lead = comparison.opportunity_id
        self.assertEqual(lead.name, "Online motor quote – Peter Kamau (KDA 123A)")
        self.assertEqual(set(lead.tag_ids.mapped("name")) & {"Online quote", "Motor"},
                         {"Online quote", "Motor"})
        self.assertEqual(lead.source_id.name, "Online motor quote")
        self.assertFalse(lead.user_id, "for the team to pick up")
        note = lead.message_ids.filtered(lambda m: "Vehicle value, year and use" in str(m.body))
        self.assertTrue(note)

        message = comparison._whatsapp_message("https://example.com/q")
        self.assertIn("here is your motor insurance quote %s" % comparison.name, message)
        self.assertIn("Test Definite – Comprehensive", message)

        values = self.Online.quote_values(comparison, comparison.odin_access_token)
        self.assertEqual(values["kind"], "motor")
        self.assertEqual(values["about"], "KDA 123A · Toyota Fielder")
        plan = values["plans"][0]
        self.assertEqual(plan["insurer"], "Test Definite")
        self.assertEqual(plan["name"], "Full cover (comprehensive)")
        sections = {s["label"]: s["text"] for s in plan["sections"]}
        self.assertIn("800,000", sections["Your vehicle"])
        self.assertEqual(sections["Riots, strikes & terrorism"], "included")
        self.assertIn("Excess protector", sections)
        self.assertTrue(plan["monthly"])

    def test_change_answers(self):
        self.Online.submit(self.vehicle(registration="KDA 123A"),
                           {str(self.definite_private.id): ["courtesy10"]},
                           [self.definite_private.id], self.contact(), {})
        first = self.Comparison.search([("odin_channel", "=", "online")], order="id desc", limit=1)
        props = self.Online.page_props(quote_id=first.id, quote_token=first.odin_access_token)
        self.assertEqual(props["prefill"]["vehicle"]["class_id"], self.private.id)
        self.assertEqual(props["prefill"]["vehicle"]["value"], 800000)
        self.assertEqual(props["prefill"]["registration"], "KDA 123A")
        self.assertEqual(props["prefill"]["extras"], {str(self.definite_private.id): ["courtesy10"]})
        self.assertEqual(props["tokens"]["quote_id"], first.id)
        medical = self.env["odin.medical.quote.online"].page_props(
            quote_id=first.id, quote_token=first.odin_access_token)
        self.assertFalse(medical["tokens"], "a motor quote is not changed on the medical page")

    def test_share_motor_link(self):
        lead = self.env["crm.lead"].create({
            "name": "Fleet enquiry", "contact_name": "Peter Kamau", "phone": "0722 555 666",
            "tag_ids": [(0, 0, {"name": "Motor"})]})
        action = lead.with_user(self.agent).action_odin_online_share()
        self.assertEqual(action["context"]["default_quote_type"], "motor")
        wizard = self.env[action["res_model"]].with_user(self.agent).with_context(
            **action["context"]).create({})
        self.assertIn("/motor-quote?", wizard.url)
        self.assertIn("compare motor insurance prices", wizard.message)


@tagged("post_install", "-at_install")
class TestOnlineMotorPages(OnlineMotorCase, HttpCase):

    def test_pages(self):
        page = self.url_open("/motor-quote")
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="odin_motor_quote_online.OnlineMotorQuote"', page.text)
        result = self.make_jsonrpc_request("/motor-quote/options", {"vehicle": self.vehicle()})
        self.assertEqual(len(result["plans"]), 2)
        self.assertEqual(self.make_jsonrpc_request("/motor-quote/options", {"vehicle": {}}),
                         {"error": "Tell us what you drive."})
        result = self.make_jsonrpc_request("/motor-quote/submit", {
            "vehicle": self.vehicle(), "extras": {}, "chosen": [self.fidelity_low.id],
            "contact": self.contact(), "tokens": {}})
        path = "/motor-quote" + result["url"].split("/motor-quote", 1)[1]
        page = self.url_open(path)
        self.assertEqual(page.status_code, 200)
        text = html2plaintext(page.text)
        for expected in ("here is your quote", "Test Fidelity", "Full cover (comprehensive)",
                         "Your vehicle", "a year", "Talk to an advisor"):
            self.assertIn(expected, text)
        token = path.split("token=")[1]
        comparison = self.Comparison.search([("odin_channel", "=", "online")], order="id desc", limit=1)
        pdf = self.url_open("/motor-quote/q/%s/pdf?token=%s" % (comparison.id, token))
        self.assertEqual(pdf.status_code, 200)
        self.assertIn("Motor%20quotation", pdf.headers["Content-Disposition"])
