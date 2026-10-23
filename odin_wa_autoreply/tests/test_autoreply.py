from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from odoo.tests import new_test_user, tagged
from odoo.tests.common import BaseCase, freeze_time

from odoo.addons.odin_wa_autoreply.models import classifier
from odoo.addons.whatsapp_connector.tests.common import WhatsappCase

# Wednesday 21 Oct 2026; Nairobi is UTC+3.
WED_10AM = datetime(2026, 10, 21, 7, 0)
WED_8PM = datetime(2026, 10, 21, 17, 0)
SAT_2PM = datetime(2026, 10, 24, 11, 0)


@tagged("post_install", "-at_install")
class TestClassifier(BaseCase):
    """What customers actually wrote to the Ads number, and what they get."""

    def test_real_messages(self):
        for text, kind, hints in (
            ("Third-party ya one month ni how much ya Toyota ist year 2005", classifier.MOTOR,
             {"vehicle": "odin_motor_quote.class_private", "cover": "tpo", "period": 1}),
            ("How much is annual insurance cover for a commercial pick-up 2tonn ,and 2 passenger's.",
             classifier.MOTOR, {"vehicle": "odin_motor_quote.class_pickup"}),
            ("Hello, how much is a third party insurance cover?", classifier.MOTOR, {"cover": "tpo"}),
            ("Am interested in a medical cover for a family of 4. Kindly share what options you have",
             classifier.MEDICAL, {"who": "family"}),
            ("Hello, can I get a medical cover quotation for a couple, with 3 kids",
             classifier.MEDICAL, {"who": "family"}),
            ("Hi, can I get detailed information on the benefits and the list of hospitals and a "
             "quote as well for health insurance cover for one person",
             classifier.MEDICAL, {"who": "me", "question": True}),
            ("Can i get a comprehensive medical insuarance for my familly me dad mum and my 3 young "
             "siblings", classifier.OTHER, {}),
            ("Personal accident for automobile driver how much ?", classifier.OTHER, {}),
            ("UJHL8AQ1AI Confirmed. Ksh1,000.00 sent to BIZSURE INSURANCE BROKERS LIMITED. Kindly "
             "nishughulikie its now an hour and am on the road", classifier.SERVICE, {}),
            ("Hi", classifier.BOTH, {}),
            ("", classifier.BOTH, {}),
        ):
            self.assertEqual(classifier.classify(text), (kind, hints), text)

    def test_stop(self):
        self.assertTrue(classifier.is_stop(" STOP "))
        self.assertFalse(classifier.is_stop("stop sending me the old quote, send the new one"))


class AutoreplyCase(WhatsappCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account.write({"odin_autoreply": True, "odin_escalate_user_ids": [(6, 0, cls.manager.ids)]})
        cls.Channel = cls.env["discuss.channel"]
        cls.count = 0

    def receive(self, text, phone="+254722000111", name="Peter Kamau", msg_type="text"):
        type(self).count += 1
        identity = {"bsuid": "KE.%s" % phone[-9:], "wa_id": phone.lstrip("+"), "phone": phone,
                    "name": name}
        message = {"id": "wamid.%s" % self.count, "type": msg_type,
                   "timestamp": str(int(datetime.now().timestamp()))}
        message[msg_type] = {"body": text} if msg_type == "text" else {"id": "media%s" % self.count}
        channel, _created = self.Channel.sudo()._wa_get_or_create_conversation(self.account, identity)
        channel._wa_receive(self.account, message, {}, identity)
        return channel

    def run_cron(self):
        self.Channel._odin_autoreply_cron()

    def autoreplies(self, channel):
        return channel.wa_message_ids.filtered("odin_auto")


@tagged("post_install", "-at_install")
class TestHours(AutoreplyCase):

    def test_business_hours(self):
        account = self.account
        self.assertTrue(account._odin_is_open(WED_10AM))
        self.assertFalse(account._odin_is_open(WED_8PM))
        self.assertFalse(account._odin_is_open(SAT_2PM), "Saturdays until 1pm")
        self.assertEqual(account._odin_when_text(WED_10AM), "shortly")
        self.assertEqual(account._odin_when_text(WED_8PM), "from 8am tomorrow")
        self.assertEqual(account._odin_when_text(SAT_2PM), "from 8am on Monday")
        # Wednesday 8pm to Thursday 10am: 2 working hours.
        self.assertEqual(account._odin_working_seconds(WED_8PM, WED_8PM + timedelta(hours=14)), 7200)


@tagged("post_install", "-at_install")
class TestAutoreply(AutoreplyCase):

    def test_waits_ten_minutes_in_business_hours(self):
        if "odin.motor.quote.online" not in self.env:
            self.skipTest("the motor quote page is not installed")
        with freeze_time(WED_10AM):
            channel = self.receive("Hello, how much is third party for my car KDA 123A?")
            self.assertEqual(channel.odin_wait_since, WED_10AM)
            self.assertEqual(channel.odin_autoreply_due, WED_10AM + timedelta(minutes=10))
        with freeze_time(WED_10AM + timedelta(minutes=5)):
            self.run_cron()
            self.assertFalse(self.autoreplies(channel), "the agents still have time")
        with freeze_time(WED_10AM + timedelta(minutes=11)):
            self.run_cron()
        sent = self.autoreplies(channel)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent.status, "queued")
        self.assertEqual(channel.odin_autoreply_kind, "motor")
        self.assertIn("Hi Peter, thanks for contacting", sent.body)
        self.assertIn("While we get an advisor for you", sent.body)
        link = next(word for word in sent.body.split() if "/motor-quote?" in word)
        query = parse_qs(urlparse(link).query)
        lead = channel.wa_lead_id
        self.assertTrue(lead, "the conversation has its lead")
        self.assertEqual(query["lead"], [self.env["odin.motor.quote.online"].lead_token(lead)])
        self.assertEqual(query["cover"], ["tpo"])
        self.assertEqual(query["class_id"], [str(self.env.ref("odin_motor_quote.class_private").id)])
        self.assertFalse(lead.user_id, "nobody owns it yet: the online quotes pool")
        self.assertTrue(lead.message_ids.filtered(lambda m: "auto-reply sent" in str(m.body)))
        self.assertFalse(channel.wa_last_user_message_at, "an auto-reply is not a person's reply")
        self.assertTrue(channel.odin_wait_since, "still waiting for a person")

        # The page opens with what the message said.
        props = self.env["odin.motor.quote.online"].page_props(
            lead_token=query["lead"][0], hints={k: v[0] for k, v in query.items()})
        self.assertEqual(props["prefill"]["vehicle"]["cover"], "tpo")
        self.assertEqual(props["prefill"]["name"], "Peter Kamau")

        # The agent answers: the wait is over.
        with freeze_time(WED_10AM + timedelta(minutes=20)):
            channel.with_user(self.user_a).message_post(body="Hello Peter", message_type="comment")
        self.assertFalse(channel.odin_wait_since)
        self.assertEqual(len(self.autoreplies(channel)), 1)

    def test_after_hours_at_once(self):
        with freeze_time(WED_8PM):
            channel = self.receive("I want medical cover for me, my wife and 2 kids")
            self.run_cron()
        sent = self.autoreplies(channel)
        self.assertEqual(channel.odin_autoreply_kind, "medical")
        self.assertIn("We're offline right now, but you can see medical cover prices", sent.body)
        self.assertIn("An advisor will also reply here from 8am tomorrow.", sent.body)
        self.assertIn("who=family", sent.body)

    def test_agent_answers_in_time(self):
        with freeze_time(WED_10AM):
            channel = self.receive("Hi")
        with freeze_time(WED_10AM + timedelta(minutes=4)):
            channel.with_user(self.user_b).message_post(body="Hello, how can we help?",
                                                       message_type="comment")
        with freeze_time(WED_10AM + timedelta(minutes=11)):
            self.run_cron()
            channel = self.receive("Medical for my family please")
            self.run_cron()
        self.assertFalse(self.autoreplies(channel), "a person is talking with them")

    def test_vague_gets_both_links(self):
        with freeze_time(WED_8PM):
            channel = self.receive("", msg_type="audio")
            self.run_cron()
        body = self.autoreplies(channel).body
        self.assertIn("Medical cover: ", body)
        self.assertIn("Motor cover: ", body)
        self.assertIn("Or just tell us what you need", body)

    def test_service_is_escalated(self):
        with freeze_time(WED_8PM):
            channel = self.receive("UJHL8AQ1AI Confirmed. Ksh1,000.00 sent to BIZSURE for account "
                                   "KAZ458Q. Kindly nishughulikie")
            self.run_cron()
        body = self.autoreplies(channel).body
        self.assertIn("an advisor is attending to it", body)
        self.assertNotIn("http", body, "no quote link for a paying customer")
        todo = channel.wa_lead_id.activity_ids
        self.assertEqual(todo.user_id, self.manager)
        self.assertIn("attend to", todo.summary)

    def test_existing_client_hears_from_their_agent(self):
        partner = self.env["res.partner"].create({"name": "Mary Wanjiku", "phone": "+254733000222"})
        order = self.env["sale.order"].create({"partner_id": partner.id, "user_id": self.user_b.id})
        order.state = "sale"
        with freeze_time(WED_10AM):
            channel = self.receive("Hello, about my motor cover", phone="+254733000222",
                                   name="Mary Wanjiku")
        with freeze_time(WED_10AM + timedelta(minutes=11)):
            self.run_cron()
        body = self.autoreplies(channel).body
        self.assertIn("Sarah will get back to you here shortly", body)
        self.assertEqual(channel.odin_autoreply_kind, "client")
        self.assertEqual(channel.wa_lead_id.activity_ids.user_id, self.user_b)

    def test_stop_staff_and_once_a_day(self):
        with freeze_time(WED_8PM):
            channel = self.receive("STOP")
            self.run_cron()
            self.assertTrue(channel.wa_partner_id.odin_wa_no_autoreply)
            self.receive("hello?")
            self.run_cron()
            self.assertFalse(self.autoreplies(channel))

            staffer = new_test_user(self.env, login="staffer", name="Staffer", groups="base.group_user")
            staffer.partner_id.phone = "+254711999888"
            staff = self.receive("testing the number", phone="+254711999888", name="Staffer")
            self.assertEqual(staff.wa_partner_id, staffer.partner_id)
            self.run_cron()
            self.assertFalse(self.autoreplies(staff), "staff testing the number")

            once = self.receive("Hi", phone="+254744555666", name="Jane")
            self.run_cron()
            self.receive("Hello again", phone="+254744555666", name="Jane")
            self.run_cron()
            self.assertEqual(len(self.autoreplies(once)), 1)

    def test_team_then_manager_are_told(self):
        with freeze_time(WED_10AM):
            channel = self.receive("Hi, motor insurance for my Probox")
        with freeze_time(WED_10AM + timedelta(hours=1, minutes=59)):
            self.run_cron()
            self.assertFalse(channel.odin_team_alerted)
        with freeze_time(WED_10AM + timedelta(hours=2, minutes=1)):
            self.run_cron()
        self.assertTrue(channel.odin_team_alerted)
        told = channel.wa_lead_id.message_ids.filtered(lambda m: "waiting for a reply" in str(m.body))
        self.assertEqual(told.partner_ids, (self.user_a | self.user_b | self.user_c).partner_id,
                         "the Notify users")
        with freeze_time(WED_10AM + timedelta(hours=19, minutes=59)):
            self.run_cron()
            self.assertFalse(channel.odin_manager_alerted)
        with freeze_time(WED_10AM + timedelta(hours=20, minutes=1)):
            self.run_cron()
        self.assertTrue(channel.odin_manager_alerted)
        todo = channel.wa_lead_id.activity_ids.filtered(lambda a: a.user_id == self.manager)
        self.assertIn("before 10am 22 Oct", todo.summary)
        self.assertIn("Pick up the conversation", str(todo.note))

    def test_off(self):
        self.account.odin_autoreply = False
        with freeze_time(WED_8PM):
            channel = self.receive("Medical cover please")
            self.run_cron()
        self.assertFalse(self.autoreplies(channel))
        self.assertFalse(channel.odin_autoreply_due)
