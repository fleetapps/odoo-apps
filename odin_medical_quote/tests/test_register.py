from odoo.tests import new_test_user, tagged

from .common import MedicalQuoteCase


@tagged("post_install", "-at_install")
class TestRegister(MedicalQuoteCase):
    """The list of quotes the team works from, and who places the cover once
    the client has accepted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.placer = new_test_user(
            cls.env, login="quote.placer", name="Cover Placer",
            groups="sales_team.group_sale_salesman")

    def setUp(self):
        super().setUp()
        result = self.search()
        afya = self.plan(result, self.afya)
        saved = self.Quote.save_quote({
            "customer": {"source": "new", "name": "Henry Otieno", "phone": "0798 726 720",
                         "email": "henry@example.com"},
            "cover": self.cover(),
            "plans": [
                {"template_id": self.afya.id, "options": [self.option(afya, "outpatient")]},
                {"template_id": self.jamii.id, "options": [], "recommended": True},
            ],
        })
        self.Comparison = self.env["odin.quote.comparison"].with_user(self.agent)
        self.comparison = self.Comparison.browse(saved["comparison"]["id"])
        orders = self.comparison.plan_ids.order_id
        self.jamii_order = orders.filtered(lambda o: o.sale_order_template_id == self.jamii)
        self.afya_order = orders - self.jamii_order

    def accept(self, order, **values):
        action = self.comparison.action_odin_accept()
        wizard = self.env["odin.medical.quote.accept"].with_user(self.agent).with_context(
            **action["context"]).create(dict({"order_id": order.id, "payment": "later"}, **values))
        wizard.action_accept()
        return wizard

    def place_cover_activity(self):
        return self.comparison.activity_ids.filtered(
            lambda a: a.activity_type_id == self.env.ref(
                "odin_medical_quote.mail_activity_type_place_cover"))

    def test_summaries(self):
        self.assertEqual(self.comparison.odin_cover_summary, "2 adults, 3 children")
        self.assertIn(self.afya.name, self.comparison.odin_plan_summary)
        self.assertIn(self.jamii.name, self.comparison.odin_plan_summary)
        self.assertEqual(self.comparison.odin_amount_total, self.jamii_order.amount_total,
                         "the recommended plan's premium")
        self.accept(self.afya_order)
        self.assertEqual(self.comparison.odin_plan_summary, self.afya.name, "only the plan taken")
        self.assertEqual(self.comparison.odin_amount_total, self.afya_order.amount_total)

    def test_declined_still_says_what_was_offered(self):
        self.comparison._odin_decline()
        self.assertIn(self.jamii.name, self.comparison.odin_plan_summary)
        self.assertTrue(self.comparison.odin_amount_total)

    def test_find_by_phone_as_typed(self):
        for typed in ("0798 726 720", "+254798726720", "henry@example", "Otieno"):
            self.assertEqual(
                self.Comparison.search([("odin_customer_search", "ilike", typed)]),
                self.comparison, typed)
        self.assertFalse(self.Comparison.search([("odin_customer_search", "ilike", "0711 000 000")]))

    def test_hand_over_to_company_person(self):
        self.company.odin_quote_handler_id = self.placer
        wizard = self.accept(self.jamii_order)
        self.assertEqual(wizard.handler_id, self.placer, "offered by default")
        self.assertEqual(self.comparison.odin_handler_id, self.placer)
        activity = self.place_cover_activity()
        self.assertEqual(activity.user_id, self.placer)
        self.assertIn(self.jamii.name, activity.summary)
        self.assertIn("proposal form", str(activity.note), "the checklist")
        self.assertIn(self.placer.partner_id, self.comparison.message_partner_ids)
        self.assertEqual(self.Comparison.search([("odin_placement_pending", "=", True)]),
                         self.comparison)

        # Handed to someone else: the to-do goes with it.
        self.comparison.odin_handler_id = self.agent
        self.assertEqual(self.place_cover_activity().user_id, self.agent)

        self.place_cover_activity().action_feedback(feedback="Cover placed")
        self.assertFalse(self.comparison.odin_placement_pending)
        self.assertFalse(self.Comparison.search([("odin_placement_pending", "=", True)]))

    def test_hand_over_picked_in_the_dialog(self):
        self.accept(self.jamii_order, handler_id=self.placer.id)
        self.assertEqual(self.comparison.odin_handler_id, self.placer)
        self.assertEqual(self.place_cover_activity().user_id, self.placer)

    def test_hand_over_when_confirmed_elsewhere(self):
        """Without a company person the salesperson places the cover, and a
        later Client accepted can pass it on."""
        self.jamii_order.action_confirm()
        self.assertEqual(self.comparison.odin_handler_id, self.agent)
        self.assertEqual(len(self.place_cover_activity()), 1)
        self.accept(self.jamii_order, handler_id=self.placer.id)
        self.assertEqual(self.comparison.odin_handler_id, self.placer)
        self.assertEqual(self.place_cover_activity().user_id, self.placer, "moved, not doubled")

    def test_saved_screen_says_who(self):
        self.accept(self.jamii_order, handler_id=self.placer.id)
        loaded = self.Quote.load_quote(self.comparison.id)
        self.assertEqual(loaded["comparison"]["handler"], "Cover Placer")

    def test_ages_from_the_online_form(self):
        params = dict(self.comparison.odin_builder_params)
        params["cover"] = dict(params["cover"], ages_only=True, principal_age=35, spouse_age=28)
        self.comparison.odin_builder_params = params
        members = dict(self.comparison._odin_export_members())
        self.assertEqual(members["Principal age"], "35")
        self.assertEqual(members["Spouse age"], "28")
        self.assertNotIn("Principal DOB", members)
