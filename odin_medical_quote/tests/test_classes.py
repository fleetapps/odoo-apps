from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged
from odoo.tools import html2plaintext

from .common import MedicalQuoteCase


class ClassCase(MedicalQuoteCase):
    """An Accident class added by the office, as a manager would: it asks
    for the sum insured and how many people, and two insurers sell it.

    *Shield* prices per person on two bands of the sum insured -- 0.5% with
    a minimum of 1,000 up to 999,999, then 0.4% -- with medical expenses
    to add at 500 a person and last expense always included. *Umbrella*
    sells one plan, a fixed 3,000 a policy, for sums up to 500,000.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["odin.insurance.class"].search([("engine", "=", "general")]).active = False
        cls.accident = cls.env["odin.insurance.class"].with_user(cls.agent).create({
            "name": "Accident",
            "description": "Personal accident cover",
            "icon": "fa-ambulance",
            "ask_amount": True,
            "amount_label": "Sum insured",
            "ask_people": True,
            "people_label": "People covered",
            "details_label": "Occupation",
        })
        cls.accident.sudo().product_id.taxes_id = [Command.set(cls.levies.ids)]
        cls.env.ref("odin_medical_quote.product_general_stamp_duty").list_price = 40
        Partner = cls.env["res.partner"]
        cls.shield = Partner.create({"name": "Test Shield", "is_company": True})
        cls.umbrella = Partner.create({"name": "Test Umbrella", "is_company": True})
        Product = cls.env["odin.cover.product"].with_user(cls.agent)
        cls.shield_pa = Product.create({
            "name": "Shield Personal Accident",
            "line": "accident",
            "insurer_id": cls.shield.id,
            "per_person": True,
            "benefits": "Death: 1,000,000\nPermanent total disablement: 1,000,000",
            "note": "Cover runs 24 hours a day, worldwide.",
            "band_ids": [
                Command.create({"amount_max": 999999, "rate": 0.5, "min_premium": 1000}),
                Command.create({"amount_min": 1000000, "rate": 0.4}),
            ],
            "extra_ids": [
                Command.create({"name": "Medical expenses", "offered": "optional",
                                "amount": 500, "per_person": True}),
                Command.create({"name": "Last expense", "offered": "included"}),
            ],
        })
        cls.umbrella_pa = Product.create({
            "name": "Umbrella Accident Plan",
            "line": "accident",
            "insurer_id": cls.umbrella.id,
            "band_ids": [Command.create({"label": "Plan A", "amount_max": 500000,
                                         "flat_premium": 3000})],
        })
        cls.General = cls.env["odin.general.quote"].with_user(cls.agent)

    def risk(self, **values):
        return dict({"class_id": self.accident.id, "amount": "1,000,000", "people": 3,
                     "details": "Teachers"}, **values)

    def plans(self, **risk):
        return self.General.search_plans({"cover": self.risk(**risk)})

    def save(self, **values):
        params = dict({
            "customer": {"source": "new", "name": "Grace Wanjiru", "phone": "0722000111"},
            "cover": self.risk(),
            "plans": [{"product_id": self.shield_pa.id, "options": [
                "extra_%s" % self.shield_pa.extra_ids[0].id], "discount": "10%"}],
        }, **values)
        saved = self.General.save_quote(params)
        return self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])


@tagged("post_install", "-at_install")
class TestInsuranceClasses(ClassCase):

    def test_class_made_by_the_office(self):
        self.assertEqual(self.accident.code, "accident")
        self.assertEqual(self.accident.engine, "general", "a class added is quoted on the standard screen")
        self.assertEqual(self.accident.product_id.name, "Accident premium")
        again = self.env["odin.insurance.class"].create({"name": "Accident"})
        self.assertEqual(again.code, "accident_2", "codes never collide")
        medical = self.env.ref("odin_medical_quote.insurance_class_medical")
        self.assertEqual((medical.code, medical.engine), ("medical", "medical"))

    def test_everywhere_a_class_is_chosen(self):
        for model, field in (("odin.cover.product", "line"),
                             ("odin.quote.comparison", "odin_quote_type"),
                             ("odin.renewal", "line")):
            options = dict(self.env[model]._fields[field]._description_selection(self.env))
            self.assertEqual(options.get("accident"), "Accident", "%s.%s" % (model, field))
            self.assertEqual(options.get("medical"), "Medical")
        types = {t["code"]: t for t in self.env["odin.insurance.class"].odin_quote_types()}
        self.assertTrue(types["accident"]["available"])
        self.assertEqual(types["accident"]["products"], 2)
        self.assertEqual(types["accident"]["action"]["tag"], "odin_general_quote")
        self.assertEqual(types["accident"]["action"]["params"], {"class_id": self.accident.id})
        self.assertEqual(types["medical"]["action"]["tag"], "odin_medical_quote")

    def test_class_without_products_says_so(self):
        travel = self.env["odin.insurance.class"].create({"name": "Travel"})
        types = {t["code"]: t for t in self.env["odin.insurance.class"].odin_quote_types()}
        self.assertFalse(types["travel"]["available"], "nothing to quote yet")
        self.assertEqual(types["travel"]["products"], 0)
        travel.unlink()

    def test_archived_and_deleted(self):
        with self.assertRaisesRegex(UserError, "Archive it instead"):
            self.accident.unlink()
        with self.assertRaisesRegex(UserError, "cannot be deleted"):
            self.env.ref("odin_medical_quote.insurance_class_medical").unlink()
        self.accident.active = False
        types = [t["code"] for t in self.env["odin.insurance.class"].odin_quote_types()]
        self.assertNotIn("accident", types, "an archived class leaves New quote")
        options = dict(self.env["odin.cover.product"]._fields["line"]._description_selection(self.env))
        self.assertEqual(options["accident"], "Accident (archived)",
                         "its products keep their class")
        self.accident.active = True
        self.accident.name = "Personal Accident"
        self.assertEqual(self.accident.code, "accident", "a rename moves nothing")
        with self.assertRaises(UserError):
            self.accident.engine = "medical"

    def test_product_stays_on_its_screen(self):
        with self.assertRaisesRegex(UserError, "another screen"):
            self.shield_pa.line = "medical"
        fire = self.env["odin.insurance.class"].create({"name": "Fire"})
        self.umbrella_pa.line = fire.code
        self.assertEqual(self.umbrella_pa.odin_engine, "general")

    def test_product_checks_and_try(self):
        self.assertFalse(self.shield_pa.warning_html)
        low = self.shield_pa.band_ids.filtered(lambda b: not b.amount_min)
        low.amount_max = 1200000
        self.assertIn("overlap", self.shield_pa.warning_html)
        low.amount_max = 999999
        self.assertEqual(self.shield_pa.summary, "Per person · 2 bands · 2 extras")
        self.shield_pa.write({"try_amount": 400000, "try_people": 2})
        text = html2plaintext(self.shield_pa.try_html)
        # 0.5% of 400,000 is 2,000 a person: 4,000, levies 18, stamp duty 40.
        self.assertIn("4,058.00", text)
        self.assertIn("Medical expenses", text)
        copy = self.shield_pa.copy()
        self.assertEqual(len(copy.band_ids), 2)
        self.assertEqual(len(copy.extra_ids), 2)
        self.assertFalse(copy.active, "a copy is off until it is checked")


@tagged("post_install", "-at_install")
class TestGeneralQuote(ClassCase):

    def test_prices_every_insurer(self):
        result = self.plans()
        self.assertEqual([p["id"] for p in result["plans"]], [self.shield_pa.id])
        plan = result["plans"][0]
        self.assertEqual(plan["insurer"], "Test Shield")
        self.assertEqual(plan["limit"], 1000000)
        self.assertEqual(plan["limit_label"], "Sum insured")
        self.assertEqual(plan["core"]["amount"], 12000, "0.4% of 1,000,000 for each of 3 people")
        parts = plan["core"]["parts"]
        self.assertEqual((parts[0]["qty"], parts[0]["price"]), (3, 4000))
        self.assertTrue(parts[1]["included"], "last expense included")
        group = plan["groups"][0]
        self.assertEqual(group["label"], "Medical expenses")
        self.assertEqual(group["options"][0]["amount"], 1500)
        self.assertEqual(result["unavailable"][0]["reason"], "Amounts up to 500,000.")
        self.assertEqual(result["risk"]["people"], 3)

        small = self.plans(amount="100000", people=1)
        amounts = {p["id"]: p["core"]["amount"] for p in small["plans"]}
        self.assertEqual(amounts, {self.umbrella_pa.id: 3000, self.shield_pa.id: 1000},
                         "the minimum premium; Umbrella's fixed premium is per policy")
        self.assertEqual(small["plans"][0]["id"], self.shield_pa.id, "cheapest first")

    def test_what_must_be_entered(self):
        with self.assertRaisesRegex(UserError, "sum insured"):
            self.plans(amount="")
        with self.assertRaisesRegex(UserError, "how many people"):
            self.plans(people=0)
        with self.assertRaisesRegex(UserError, "what kind of insurance"):
            self.plans(class_id=self.env.ref("odin_medical_quote.insurance_class_medical").id)

    def test_totals(self):
        totals = self.General.compute_totals({
            "cover": self.risk(),
            "plans": [{"product_id": self.shield_pa.id, "discount": "10%",
                       "options": ["extra_%s" % self.shield_pa.extra_ids[0].id]}],
        })[str(self.shield_pa.id)]
        self.assertEqual(totals["premium"], 13500)
        self.assertEqual(totals["discount"], 1350)
        self.assertEqual(totals["charges"], 40)
        # 0.45% of 12,150 is 54.675: .67 or .68 as the company rounds its taxes.
        self.assertAlmostEqual(totals["taxes"], 54.675, delta=0.006)
        self.assertAlmostEqual(totals["total"], 12150 + 54.675 + 40, delta=0.006)

    def test_save(self):
        comparison = self.save()
        self.assertEqual(comparison.odin_quote_type, "accident")
        self.assertEqual(comparison.title, "Accident Insurance")
        lead = comparison.opportunity_id
        self.assertEqual(lead.name, "Accident cover – Grace Wanjiru (Teachers)")
        self.assertIn("Accident", lead.tag_ids.mapped("name"))
        order = comparison.plan_ids.order_id
        self.assertEqual(order.odin_cover_product_id, self.shield_pa)
        self.assertAlmostEqual(order.amount_total, 12150 + 54.675 + 40, delta=0.006)
        self.assertEqual(order._odin_plan_label(), "Test Shield – Shield Personal Accident")
        self.assertEqual(comparison.plan_ids.name, "Test Shield")
        self.assertEqual(comparison.plan_ids.subtitle, "Shield Personal Accident")
        self.assertIn("24 hours", html2plaintext(order.note), "the product's terms")
        sections = {s["key"]: s for s in order._client_sections() if s["included"]}
        self.assertIn("Death: 1,000,000", sections["basic"]["notes"][0], "what it pays out")
        self.assertEqual(sections["extra_%s" % self.shield_pa.extra_ids[0].id]["amount"], 1500)

        self.assertEqual(comparison._odin_export_member_headings()[0], "Cover details")
        self.assertEqual(comparison._odin_export_members(), [
            ("Cover", "Accident"), ("Sum insured", "1,000,000"), ("People covered", "3"),
            ("Occupation", "Teachers")])
        self.assertEqual(comparison._odin_email_template(),
                         self.env.ref("odin_medical_quote.mail_template_general_quote"))
        self.assertIn("Accident quotation", comparison._odin_export_filename())
        html, _kind = self.env["ir.actions.report"].with_user(self.agent)._render_qweb_html(
            "odin_medical_quote.action_report_medical_quotation", comparison.ids)
        text = html2plaintext(html.decode())
        for expected in ("Cover details", "Sum insured", "Teachers", "Test Shield",
                         "Medical expenses", "Death: 1,000,000"):
            self.assertIn(expected, text)

        action = comparison.action_odin_edit()
        self.assertEqual(action["tag"], "odin_general_quote")
        self.assertEqual(action["params"], {"comparison_id": comparison.id,
                                            "class_id": self.accident.id})
        loaded = self.General.load_quote(comparison.id)
        self.assertEqual(loaded["orders"][0]["key"], self.shield_pa.id)
        self.assertEqual(loaded["params"]["cover"]["class_id"], self.accident.id)

    def test_save_again_and_accept(self):
        comparison = self.save()
        order = comparison.plan_ids.order_id
        params = dict(comparison.odin_builder_params, comparison_id=comparison.id)
        params["plans"] = params["plans"] + [{"product_id": self.umbrella_pa.id, "options": []}]
        params["cover"] = self.risk(amount="400000")
        self.General.save_quote(params)
        shield = comparison.plan_ids.filtered(
            lambda p: p.order_id.odin_cover_product_id == self.shield_pa)
        self.assertEqual(shield.order_id, order, "rebuilt in place")
        self.assertEqual(len(comparison.plan_ids), 2)
        # 0.5% of 400,000 for 3 people, medical expenses, 10% off, stamp duty.
        self.assertAlmostEqual(order.amount_untaxed, 3 * 2000 + 1500 - 750 + 40, places=2)

        action = comparison.action_odin_accept()
        wizard = self.env["odin.medical.quote.accept"].with_user(self.agent).with_context(
            **action["context"]).create({"order_id": order.id})
        wizard.action_accept()
        self.assertEqual(comparison.state, "accepted")
        renewal = self.env["odin.renewal"].search([("order_id", "=", order.id)])
        self.assertEqual(renewal.line, "accident")
        self.assertEqual(renewal.product_id, self.shield_pa)
        self.assertEqual(renewal.insurer_id, self.shield)
        self.assertTrue(renewal.name.startswith("Accident – "))
        start = renewal.action_odin_start_quote()
        self.assertEqual(start["tag"], "odin_general_quote")
        self.assertEqual(start["params"]["class_id"], self.accident.id)
        self.assertEqual(start["params"]["prefill_comparison_id"], comparison.id)
        prefill = self.General.load_prefill(comparison.id)
        self.assertNotIn("customer", prefill)
        self.assertEqual(prefill["cover"]["amount"], "400000")


@tagged("post_install", "-at_install")
class TestGeneralQuoteTour(ClassCase, HttpCase):
    """New quote ▸ Accident in a real browser, as a salesperson."""

    def test_tour(self):
        self.agent.password = "quote.agent"
        self.start_tour("/odoo/action-odin_medical_quote.action_quote_new",
                        "odin_general_quote_tour", login="quote.agent")
        comparison = self.env["odin.quote.comparison"].search(
            [("partner_id.name", "=", "Tour Accident Client")])
        self.assertEqual(comparison.odin_quote_type, "accident")
        order = comparison.plan_ids.order_id
        self.assertEqual(order.odin_cover_product_id, self.shield_pa)
        # 3 × 0.4% of 1,000,000, levies 0.45%, stamp duty 40.
        self.assertAlmostEqual(order.amount_total, 12000 + 54 + 40, places=2)
