from urllib.parse import unquote

from odoo import SUPERUSER_ID
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.odin_medical_quote.tests.common import MedicalQuoteCase


class OnlineCase(MedicalQuoteCase):
    """The rate card of odin_medical_quote's tests, quoted by a stranger."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Online = cls.env["odin.medical.quote.online"]
        cls.company.odin_online_user_id = cls.agent
        cls.family = {"who": "family", "age": 35, "partner_age": 28, "children": 3}
        cls.Comparison = cls.env["odin.quote.comparison"]

    def contact(self, **values):
        return dict({"name": "Grace Wanjiru", "phone": "0722 000 111", "email": "",
                     "contact_by": "whatsapp", "consent": True}, **values)

    def submit(self, **values):
        params = dict(family=self.family, wants=["outpatient"], chosen=[self.afya.id],
                      contact=self.contact(), tokens={})
        params.update(values)
        result = self.Online.submit(**params)
        comparison = self.Comparison.search([("odin_channel", "=", "online")], order="id desc", limit=1)
        return result, comparison


@tagged("post_install", "-at_install")
class TestOnlinePlans(OnlineCase):

    def plans(self, **values):
        return {plan["id"]: plan for plan in self.Online.options(
            dict(self.family), **values)["plans"]}

    def test_plans_in_plain_words(self):
        result = self.Online.options(self.family)
        ids = [plan["id"] for plan in result["plans"]]
        self.assertEqual(set(ids), {self.afya.id, self.jamii.id},
                         "only what a family of these ages can take")
        totals = [plan["total"] for plan in result["plans"]]
        self.assertEqual(totals, sorted(totals), "cheapest first")
        afya = result["plans"][ids.index(self.afya.id)]
        self.assertEqual(afya["name"], "Plan 1 (KES 200,000)", "no 'Below 70'")
        self.assertEqual(afya["hospital"], 15822)
        self.assertEqual(afya["benefits"], [])
        self.assertAlmostEqual(afya["total"], 15822 * 1.0045 + 40, delta=0.02,
                               msg="levies and stamp duty in the price")
        self.assertEqual(result["offered"], ["outpatient", "dental"],
                         "riders (Femina, Hosi Cash) are left to the advisor")
        self.assertEqual(result["people"], 5)

    def test_dental_comes_with_what_it_needs(self):
        plans = self.plans(wants=["dental"])
        self.assertEqual(set(plans), {self.afya.id}, "Jamii has no dental")
        benefits = {b["key"]: b for b in plans[self.afya.id]["benefits"]}
        self.assertEqual(set(benefits), {"outpatient", "dental"})
        self.assertEqual(benefits["outpatient"]["for"], "Dental care",
                         "added, and the visitor is told why")
        self.assertEqual(benefits["outpatient"]["amount"], 22130)
        self.assertEqual(benefits["dental"]["amount"], 5 * 1210, "the cheaper tier first")

    def test_tier_picked(self):
        dental = {b["key"]: b for b in self.plans(wants=["outpatient", "dental"])[
            self.afya.id]["benefits"]}["dental"]
        dearer = next(o for o in dental["options"] if o["amount"] == 5 * 1803)
        plan = self.plans(wants=["outpatient", "dental"],
                          tiers={str(self.afya.id): {"dental": dearer["key"]}})[self.afya.id]
        picked = {b["key"]: b for b in plan["benefits"]}["dental"]
        self.assertEqual(picked["option"], dearer["key"])
        self.assertEqual(picked["amount"], 5 * 1803)

    def test_link_hints(self):
        """A link may say who needs cover (a WhatsApp auto-reply does);
        anything else is ignored."""
        self.assertEqual(self.Online.page_props(hints={"who": "family"})["prefill"], {"who": "family"})
        self.assertEqual(self.Online.page_props(hints={"who": "<script>"})["prefill"], {})

    def test_only_plans_shown_online(self):
        self.jamii.odin_online = False
        self.assertEqual(set(self.plans()), {self.afya.id})
        self.assertIn(self.jamii.id, [p["id"] for p in self.Quote.search_plans(
            {"cover": self.cover()})["plans"]], "still quoted by agents")

    def test_children_only_and_ages(self):
        plans = self.plans()
        self.assertNotIn(self.jamii_children.id, plans)
        kids = self.Online.options({"who": "kids", "children": 2})["plans"]
        self.assertEqual([p["id"] for p in kids], [self.jamii_children.id])
        for family, message in (
                ({"who": "me", "age": 12}, "between 18 and 99"),
                ({"who": "family", "age": 35, "partner_age": 30, "children": 0}, "how many children"),
                ({"who": "anyone"}, "who needs cover")):
            with self.assertRaisesRegex(UserError, message):
                self.Online.options(family)


@tagged("post_install", "-at_install")
class TestOnlineSubmit(OnlineCase):

    def online_activities(self, lead):
        activity_type = self.env.ref("odin_medical_quote_online.mail_activity_type_online_quote")
        return lead.activity_ids.filtered(lambda a: a.activity_type_id == activity_type)

    def test_new_client(self):
        result, comparison = self.submit()
        self.assertIn("/medical-quote/q/%s?token=" % comparison.id, result["url"])
        self.assertTrue(comparison._odin_online_check(result["url"].split("token=")[1]))
        self.assertEqual(comparison.odin_channel, "online")
        self.assertEqual(comparison.odin_contact_by, "whatsapp")
        self.assertEqual(comparison.partner_id.name, "Grace Wanjiru")
        self.assertEqual(comparison.partner_id.phone, "+254722000111")
        self.assertEqual(comparison.odin_cover_summary, "2 adults, 3 children")
        self.assertEqual(comparison.plan_ids.order_id.sale_order_template_id, self.afya)
        cover = comparison.odin_builder_params["cover"]
        self.assertTrue(cover["ages_only"])
        self.assertEqual((cover["principal_age"], cover["spouse_age"]), (35, 28))

        lead = comparison.opportunity_id
        self.assertEqual(lead.type, "opportunity")
        self.assertIn("Online quote", lead.tag_ids.mapped("name"))
        self.assertEqual(lead.source_id.name, "Online medical quote")
        self.assertEqual(lead.user_id, self.agent, "the online quotes setting")
        activity = self.online_activities(lead)
        self.assertEqual(len(lead.activity_ids), 1, "one to-do, not save_quote's follow-up too")
        self.assertEqual(activity.user_id, self.agent)
        self.assertEqual(activity.create_uid.id, SUPERUSER_ID,
                         "given by OdooBot, so the agent is notified")
        self.assertIn("Grace Wanjiru", activity.summary)
        note = lead.message_ids.filtered(lambda m: "online quote page" in str(m.body))
        self.assertTrue(note)
        self.assertIn("Ages given", str(note.body))
        self.assertIn("Agreed to be contacted", str(note.body))

    def test_a_known_number_reveals_nothing(self):
        """Typing someone else's number or email gets a new contact: their
        name never reaches the visitor's quote. The office is told."""
        known = self.env["res.partner"].create({
            "name": "Real Customer", "phone": "+254722000111", "email": "real@example.com"})
        _result, comparison = self.submit(contact=self.contact(email="real@example.com"))
        self.assertNotEqual(comparison.partner_id, known)
        self.assertEqual(comparison.partner_id.name, "Grace Wanjiru")
        note = comparison.opportunity_id.message_ids.filtered(lambda m: "Already in Contacts" in str(m.body))
        self.assertIn("Real Customer", str(note.body))

    def test_agent_link(self):
        other = new_test_user(self.env, login="other.agent", name="Other Agent",
                              groups="sales_team.group_sale_salesman")
        _result, comparison = self.submit(tokens={"agent": self.Online.agent_token(other)})
        self.assertEqual(comparison.opportunity_id.user_id, other)
        self.assertEqual(comparison.user_id, other)
        self.assertEqual(comparison.odin_online_via, "agent")
        forged = "%s-%s" % (other.id, "x" * 24)
        _result, comparison = self.submit(tokens={"agent": forged},
                                          contact=self.contact(phone="0733 444 555"))
        self.assertEqual(comparison.opportunity_id.user_id, self.agent, "a forged link is ignored")
        self.assertEqual(comparison.odin_online_via, "public")

    def test_lead_link(self):
        lead = self.env["crm.lead"].create({
            "name": "WhatsApp — Jane", "type": "lead", "contact_name": "Jane Achieng",
            "phone": "+254711000222", "user_id": self.agent.id})
        token = self.Online.lead_token(lead)
        props = self.Online.page_props(lead_token=token)
        self.assertEqual(props["prefill"]["name"], "Jane Achieng")
        self.assertEqual(props["tokens"]["lead"], token)
        count = self.env["crm.lead"].search_count([])
        _result, comparison = self.submit(tokens={"lead": token},
                                          contact=self.contact(name="Jane Achieng"))
        self.assertEqual(comparison.opportunity_id, lead, "back on the agent's opportunity")
        self.assertEqual(self.env["crm.lead"].search_count([]), count)
        self.assertEqual(lead.type, "opportunity")
        self.assertFalse(self.Online.page_props(lead_token="%s-forged" % lead.id)["prefill"])

    def test_change_answers(self):
        result, first = self.submit()
        token = result["url"].split("token=")[1]
        props = self.Online.page_props(quote_id=first.id, quote_token=token)
        self.assertEqual(props["prefill"]["who"], "family")
        self.assertEqual(props["prefill"]["age"], 35)
        self.assertEqual(props["prefill"]["wants"], ["outpatient"])
        _result, second = self.submit(
            chosen=[self.afya.id, self.jamii.id],
            tokens={"quote_id": first.id, "quote": token})
        self.assertNotEqual(second, first, "a new quote: the first may have been worked on")
        self.assertEqual(second.opportunity_id, first.opportunity_id)
        self.assertEqual(second.partner_id, first.partner_id)

    def test_checks(self):
        for values, message in (
                ({"contact": self.contact(consent=False)}, "Tick the box"),
                ({"contact": self.contact(phone="12")}, "phone number"),
                ({"contact": self.contact(email="not-an-email")}, "email address"),
                ({"contact": self.contact(contact_by="email")}, "Enter your email"),
                ({"contact": self.contact(website="http://spam")}, "Something went wrong"),
                ({"chosen": []}, "at least one plan"),
                ({"chosen": [self.afya70.id]}, "no longer available"),
                ({"chosen": [self.afya.id, self.jamii.id, self.afya70.id, self.jamii_children.id]},
                 "up to 3")):
            with self.assertRaisesRegex(UserError, message):
                self.submit(**values)
        self.assertFalse(self.Comparison.search([("odin_channel", "=", "online")]))

    def test_caps(self):
        self.env["ir.config_parameter"].set_param(
            "odin_medical_quote_online.max_per_phone_day", 1)
        self.submit()
        with self.assertRaisesRegex(UserError, "several quotes today"):
            self.submit()

    def test_quote_by_email(self):
        _result, comparison = self.submit(contact=self.contact(email="grace@example.com"))
        self.assertEqual(comparison.state, "sent")
        sent = comparison.message_ids.filtered("attachment_ids")
        self.assertTrue(sent, "the PDF went with it")
        self.assertIn(comparison.partner_id, sent.partner_ids)

    def test_talk_to_an_advisor(self):
        _result, comparison = self.submit()
        lead = comparison.opportunity_id
        self.Online.request_callback(comparison, "call", "morning")
        self.assertEqual(comparison.odin_contact_by, "call")
        self.assertEqual(comparison.odin_contact_when, "morning")
        self.assertTrue(comparison.odin_callback_requested)
        self.assertEqual(lead.priority, "2")
        activity = self.online_activities(lead)
        self.assertEqual(len(activity), 1, "the to-do is replaced, so the agent is told again")
        self.assertIn("Call back", activity.summary)
        self.assertIn("Phone call", activity.summary)
        self.assertEqual(self.Comparison.search([("odin_callback_requested", "!=", False)]), comparison)
        # Pressed twice: one request.
        messages = len(lead.message_ids)
        self.Online.request_callback(comparison, "call", "morning")
        self.assertEqual(len(lead.message_ids), messages)
        self.assertEqual(self.online_activities(lead), activity)


@tagged("post_install", "-at_install")
class TestOnlineShare(OnlineCase):

    def test_share_with_a_lead(self):
        lead = self.env["crm.lead"].create({
            "name": "WhatsApp — Jane", "contact_name": "Jane Achieng", "phone": "0711 000 222"})
        action = lead.with_user(self.agent).action_odin_online_share()
        wizard = self.env[action["res_model"]].with_user(self.agent).with_context(
            **action["context"]).create({})
        self.assertIn("agent=%s" % self.Online.agent_token(self.agent).replace("-", "-"), wizard.url)
        self.assertIn("lead=%s" % self.Online.lead_token(lead), wizard.url)
        self.assertIn("utm_source=WhatsApp", wizard.message)
        self.assertTrue(wizard.message.startswith("Hello Jane,"))
        url = wizard.action_whatsapp()["url"]
        self.assertTrue(url.startswith("https://wa.me/254711000222?text="), url)
        self.assertTrue(lead.message_ids.filtered(lambda m: "link sent" in str(m.body)))

    def test_share_own_link(self):
        wizard = self.env["odin.medical.quote.online.share"].with_user(self.agent).create({})
        self.assertNotIn("lead=", wizard.url)
        self.assertTrue(wizard.action_whatsapp()["url"].startswith("https://wa.me/?text="))

    def test_share_quote_on_whatsapp(self):
        """An agent's quote on WhatsApp: in words, with each price, linking to
        the client's own page rather than a bare PDF."""
        saved = self.Quote.save_quote({
            "customer": {"source": "new", "name": "Amina Hassan", "phone": "0722 444 555"},
            "cover": self.cover(),
            "plans": [{"template_id": self.afya.id, "options": [], "recommended": True}],
        })
        comparison = self.Comparison.browse(saved["comparison"]["id"])
        action = comparison.with_user(self.agent).action_share_whatsapp()
        text = unquote(action["url"].split("text=", 1)[1])
        self.assertTrue(text.startswith("Hello Amina,\nhere is your medical insurance quote %s"
                                        % comparison.name), text)
        self.assertIn("(our recommendation)", text)
        self.assertIn("/medical-quote/q/%s?token=" % comparison.id, text)
        self.assertEqual(comparison.state, "sent")
        values = self.Online.quote_values(comparison, comparison.odin_access_token)
        self.assertEqual(values["advisor"], self.agent, "their agent, by name")

        # What a WhatsApp template fills its variables and button with.
        order = comparison.plan_ids.order_id
        self.assertEqual(comparison.odin_online_path, "medical-quote/q/%s?token=%s" % (
            comparison.id, comparison.odin_access_token))
        self.assertIn("{:,.2f}".format(order.amount_total), comparison.odin_share_total)
        self.assertNotIn("options", comparison.odin_share_total)
        self.assertRegex(comparison.odin_share_valid_until, r"^\d{1,2} \w{3} \d{4}$")

    def test_campaign_link(self):
        """For an ad: nobody's name on it, the source and campaign in it."""
        source = self.env["utm.source"].create({"name": "Facebook"})
        campaign = self.env["utm.campaign"].create({"name": "Family cover October"})
        wizard = self.env["odin.medical.quote.online.share"].with_user(self.agent).create({
            "audience": "campaign", "source_id": source.id, "campaign_id": campaign.id})
        self.assertEqual(wizard.link_for, "campaign")
        self.assertTrue(wizard.url.startswith("%s/medical-quote?" % wizard.get_base_url()), wizard.url)
        self.assertIn("utm_source=Facebook", wizard.url)
        self.assertIn("utm_campaign=Family+cover+October", wizard.url)
        self.assertNotIn("agent=", wizard.url)
        self.assertFalse(wizard.message)
        self.assertIn("Quote Agent", wizard.routing, "the person in Settings")
        self.company.odin_online_user_id = False
        wizard.invalidate_recordset(["routing"])
        self.assertIn("anyone in the team", wizard.routing)


@tagged("post_install", "-at_install")
class TestOnlinePool(OnlineCase):
    """An ad, the website, the plain page: nobody's link, so the quote waits
    for anyone in the team, unless the client already has an agent."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company.odin_online_user_id = False
        cls.team = cls.env["crm.team"].create({"name": "Online team"})
        cls.env["crm.team.member"].create({"crm_team_id": cls.team.id, "user_id": cls.agent.id})
        cls.company.odin_online_team_id = cls.team
        cls.other = new_test_user(cls.env, login="other.agent", name="Other Agent",
                                  groups="sales_team.group_sale_salesman")
        cls.activity_type = cls.env.ref("odin_medical_quote_online.mail_activity_type_online_quote")

    def test_waits_for_the_team(self):
        _result, comparison = self.submit()
        lead = comparison.opportunity_id
        self.assertFalse(lead.user_id)
        self.assertEqual(lead.team_id, self.team)
        self.assertFalse(comparison.user_id)
        self.assertFalse(comparison.plan_ids.order_id.user_id, "not OdooBot's quotations")
        self.assertFalse(lead.activity_ids, "no to-do until someone takes it")
        self.assertEqual(comparison.odin_online_via, "public")
        told = lead.message_ids.filtered(lambda m: "waiting to be picked up" in str(m.body))
        self.assertIn(self.agent.partner_id, told.partner_ids, "the team is notified")
        self.assertIn(comparison, self.Comparison.search([
            ("odin_channel", "=", "online"), ("user_id", "=", False)]), "To pick up")

        comparison.with_user(self.agent).action_odin_pick_up()
        self.assertEqual(lead.user_id, self.agent)
        self.assertEqual(comparison.user_id, self.agent)
        self.assertEqual(comparison.plan_ids.order_id.user_id, self.agent)
        activity = lead.activity_ids.filtered(lambda a: a.activity_type_id == self.activity_type)
        self.assertEqual(activity.user_id, self.agent)
        self.assertIn("Grace Wanjiru", activity.summary)
        self.assertTrue(lead.message_ids.filtered(lambda m: "Picked up by Quote Agent" in str(m.body)))
        with self.assertRaisesRegex(UserError, "Quote Agent has already picked this up"):
            comparison.with_user(self.other).action_odin_pick_up()

    def test_assigned_in_crm(self):
        """Given out with the Salesperson field (or CRM's assignment rules):
        the same as Assign to me."""
        _result, comparison = self.submit()
        comparison.opportunity_id.user_id = self.other
        self.assertEqual(comparison.user_id, self.other)
        self.assertEqual(comparison.opportunity_id.activity_ids.user_id, self.other)

    def test_call_before_anyone_picks_it_up(self):
        _result, comparison = self.submit()
        lead = comparison.opportunity_id
        self.Online.request_callback(comparison, "call", "morning")
        self.assertEqual(lead.priority, "2")
        self.assertFalse(lead.activity_ids)
        self.assertTrue(lead.message_ids.filtered(
            lambda m: "Nobody has picked this quote up yet" in str(m.body)
            and self.agent.partner_id in m.partner_ids))
        lead.with_user(self.agent).action_odin_pick_up()
        self.assertIn("Call back", lead.activity_ids.summary, "what the client asked for")

    def test_returning_client(self):
        """Their agent's client, back through an ad: to that agent, without
        telling the visitor who it is."""
        known = self.env["crm.lead"].create({
            "name": "Grace – motor", "type": "opportunity", "phone": "+254722000111",
            "user_id": self.other.id})
        _result, comparison = self.submit()
        lead = comparison.opportunity_id
        self.assertNotEqual(lead, known, "a new opportunity: the visitor typed the number")
        self.assertEqual(lead.user_id, self.other)
        self.assertTrue(lead.message_ids.filtered(lambda m: "Assigned to Other Agent" in str(m.body)))
        values = self.Online.quote_values(comparison, comparison.odin_access_token)
        self.assertFalse(values["advisor"], "an advisor, not their name")

    def test_agent_named_on_their_own_link(self):
        _result, comparison = self.submit(tokens={"agent": self.Online.agent_token(self.other)})
        values = self.Online.quote_values(comparison, comparison.odin_access_token)
        self.assertEqual(values["advisor"], self.other)
