from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNewCards(TransactionCase):
    """GA Community Health and Hadhi, Jubilee J Care, J Care Junior and J
    Senior, and Heritage HeriAfya as New quote prices them, each figure read
    off the insurer's own card (HeriAfya: and its quotation tool).

    Jubilee rates the family on the age of the oldest applicant: a principal
    of 35 with a spouse of 42 are both on the 41-50 rates.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Quote = cls.env["odin.medical.quote"]
        cls.jubilee = cls.env.ref("odin_bizsure_quotes.insurer_jubilee")
        cls.ga = cls.env.ref("odin_bizsure_quotes.insurer_ga")
        cls.heritage = cls.env.ref("odin_bizsure_quotes.insurer_heritage")
        cls.apa = cls.env.ref("odin_bizsure_quotes.insurer_apa")

    def plan(self, key, **cover):
        product = self.env.ref("odin_bizsure_quotes.cover_%s" % key)
        result = self.Quote.search_plans({"cover": dict(cover, template_id=product.template_id.id)})
        return (result["plans"] + result["unavailable"])[0]

    @staticmethod
    def options(plan, benefit):
        group = next(g for g in plan["groups"] if g["benefit_key"] == benefit)
        return group, [option["amount"] for option in group["options"]]

    def test_plans_installed(self):
        # GA Insurance is also the motor insurer: its motor products are not these.
        Product = self.env["odin.cover.product"].with_context(active_test=False)
        products = Product.search([("insurer_id", "in", (self.jubilee | self.ga | self.heritage).ids),
                                   ("line", "=", "medical")])
        self.assertEqual(len(products.filtered(lambda p: p.insurer_id == self.ga)), 34,
                         "26 Community Health, 8 Hadhi")
        self.assertEqual(len(products.filtered(lambda p: p.insurer_id == self.jubilee)), 17)
        self.assertEqual(len(products.filtered(lambda p: p.insurer_id == self.heritage)), 20,
                         "Options 1 to 10 per family and per person")
        off = products.filtered(lambda p: not p.active)
        self.assertEqual(sorted(off.mapped("name")), sorted(
            "GA Hadhi Health – Plan %s (KES %s), with Covid-19 cover (groups of 10 or more)" % pair
            for pair in (("A", "5,000,000"), ("B", "2,500,000"), ("C", "1,000,000"), ("D", "500,000"))),
            "Covid-enhanced Hadhi is for 10 members and above: off until the office says")
        self.assertFalse(any(off.template_id.mapped("active")))
        birdview = self.env.ref(
            "odin_bizsure_quotes.sot_birdview_bizsure_afya_plan_1_kes_200_000_below_70")
        for product in products:
            template = product.template_id
            self.assertTrue(template.odin_medical, product.name)
            self.assertEqual(template.odin_insurer_id, product.insurer_id, product.name)
            if product.insurer_id == self.heritage:
                # HeriAfya prints a member-only shared outpatient rate its own
                # quotation tool does not use: left out until Heritage says.
                self.assertIn("Shared per Family: no premium for a family of 1",
                              product.warning_html, product.name)
                self.assertNotIn("Inpatient", product.warning_html, product.name)
            else:
                self.assertFalse(product.warning_html, product.name)
            self.assertEqual(template.number_of_days, birdview.number_of_days, product.name)
            self.assertIn("statutory_charges", template.sale_order_template_line_ids.mapped(
                "benefit_key"), product.name)
        junior = self.env.ref("odin_bizsure_quotes.cover_jcj_classic").template_id
        self.assertEqual(junior.odin_plan_type, "child_only")
        self.assertEqual(junior.odin_inpatient_limit, 500000)
        senior = self.env.ref("odin_bizsure_quotes.cover_js_silver").template_id
        self.assertEqual(senior.odin_inpatient_limit, 3000000)
        spouse = self.env.ref("odin_bizsure_quotes.rate_jc_ip_cl_s41")
        self.assertEqual(spouse.name,
                         "J Care Inpatient – Classic – KES 500,000 per Family – Spouse, eldest aged 41–50")

    def test_j_care_classic(self):
        plan = self.plan("jc_classic", principal_age=35, include_spouse=True, spouse_age=42,
                         children=3)
        self.assertTrue(plan["available"], plan["reason"])
        self.assertEqual(plan["core"]["amount"], 23215 + 19453 + 3 * 10282)
        self.assertEqual(self.options(plan, "outpatient")[1], [5 * 30315])
        self.assertEqual(self.options(plan, "maternity")[1], [30129])
        dental, amounts = self.options(plan, "dental")
        self.assertEqual(amounts, [5 * 2696])
        self.assertEqual(dental["requires"], "outpatient", "dental only with outpatient")
        self.assertEqual(self.options(plan, "optical")[1], [5 * 2696])
        self.assertEqual(self.options(plan, "last_expense")[1], [4 * 1007],
                         "the spouse and three children; free for the principal")
        self.assertEqual(self.options(plan, "personal_accident")[1], [586], "the spouse only")
        self.assertEqual(self.options(plan, "enhanced_covid_19")[1],
                         [5 * 6141, 5 * 9490, 5 * 10886, 5 * 12840])

        options = [self.options(plan, key)[0]["options"][0]["key"]
                   for key in ("outpatient", "dental")]
        totals = self.Quote.compute_totals({
            "cover": {"principal_age": 35, "include_spouse": True, "spouse_age": 42,
                      "children": 3},
            "plans": [{"template_id": plan["id"], "options": options}],
        })[str(plan["id"])]
        premium = 73514 + 151575 + 13480
        self.assertEqual(totals["premium"], premium)
        # Odoo rounds each levy on each line to the cent: a few cents either way.
        self.assertAlmostEqual(totals["total"], premium * 1.0045 + 40, delta=0.05,
                               msg="0.45% levies and KES 40 stamp duty")

    def test_j_care_outpatient_shared(self):
        plan = self.plan("jc_royal", principal_age=45, include_spouse=True, spouse_age=40,
                         children=1)
        self.assertEqual(plan["core"]["amount"], 62313 + 52275 + 27444)
        group, amounts = self.options(plan, "outpatient")
        self.assertEqual(amounts, [3 * 55750, 136986], "per person, or shared M+2")
        self.assertEqual(len({option["label"] for option in group["options"]}), 2)
        bigger = self.plan("jc_royal", principal_age=45, include_spouse=True, spouse_age=40,
                           children=2)
        shared = self.options(bigger, "outpatient")[0]["options"][1]
        self.assertFalse(shared["available"], "Royal shares outpatient for up to 3 people")

    def test_j_care_age_limit(self):
        plan = self.plan("jc_classic", principal_age=60, include_spouse=True, spouse_age=65)
        self.assertFalse(plan["available"])
        self.assertIn("eldest is 65", plan["reason"])

    def test_j_care_junior(self):
        plan = self.plan("jcj_advanced", children_only=True, children=2)
        self.assertTrue(plan["available"], plan["reason"])
        self.assertEqual(plan["core"]["amount"], 2 * 30215)
        self.assertEqual(self.options(plan, "outpatient")[1], [2 * 31982])
        self.assertEqual(self.options(plan, "dental")[1], [2 * 7151])

    def test_j_senior(self):
        plan = self.plan("js_silver", principal_age=72, include_spouse=True, spouse_age=66)
        self.assertEqual(plan["core"]["amount"], 143766 + 123254)
        self.assertEqual(self.options(plan, "outpatient")[1], [2 * 76749])
        self.assertEqual(self.options(plan, "last_expense")[1], [5275], "the spouse, at 70-74")
        self.assertEqual(self.options(plan, "dental")[1], [2 * 23212])
        with_child = self.plan("js_silver", principal_age=67, children=1)
        self.assertEqual(with_child["core"]["amount"], 121067 + 20281)
        self.assertEqual(self.options(with_child, "outpatient")[1], [69771 + 39805],
                         "the child on the child rate")
        young = self.plan("js_copper", principal_age=64)
        self.assertFalse(young["available"], "J Senior is from 65")

    def test_ga(self):
        result = self.Quote.search_plans({"cover": {
            "principal_age": 35, "include_spouse": True, "spouse_age": 30, "children": 3,
            "limit_min": 1000000, "limit_max": 1000000, "insurer": self.ga.name}})
        plans = {plan["name"]: plan for plan in result["plans"]}
        self.assertEqual(sorted(plans), ["KES 1,000,000", "KES 1,000,000, with pandemic cover"])
        self.assertEqual(plans["KES 1,000,000"]["core"]["amount"], 72915)
        self.assertEqual(plans["KES 1,000,000, with pandemic cover"]["core"]["amount"], 87498)
        self.assertEqual(self.options(plans["KES 1,000,000"], "maternity")[1],
                         [13283, 17078, 18975, 20873, 22770, 34085])
        older = self.plan("ga_250k_excl", principal_age=61)
        self.assertFalse(older["available"], "last joining age 60")
        large = self.plan("ga_10m_incl", principal_age=50, include_spouse=True, spouse_age=45,
                          children=5)
        self.assertEqual(large["core"]["amount"], 236437, "M + 6")

    def test_heriafya(self):
        """As Heritage's quotation tool prices it: each adult on their own age
        group, the first child on Child, every other child on Extra."""
        family = {"principal_age": 35, "include_spouse": True, "spouse_age": 28, "children": 3}
        plan = self.plan("ha_option_3", **family)
        self.assertTrue(plan["available"], plan["reason"])
        self.assertEqual(plan["core"]["amount"], 32400 + 18360 + 13500 + 2 * 10800)
        extra = next(part for part in plan["core"]["parts"] if part["price"] == 10800)
        self.assertEqual(extra["qty"], 2)
        outpatient = self.options(plan, "outpatient")[1]
        self.assertEqual(len(outpatient), 16, "8 limits per person, 8 shared per family")
        self.assertEqual(outpatient[2], 42525 + 4 * 32400, "100,000 per person")
        self.assertEqual(outpatient[10], 90699, "100,000 shared, M + 4")
        self.assertEqual(self.options(plan, "maternity")[1],
                         [16875, 25313, 33750, 50625, 67500, 84375, 101250, 118125])
        dental = self.options(plan, "dental_and_optical")[1]
        self.assertEqual(len(dental), 32)
        self.assertEqual(dental[0], 2 * 3780, "twin, 10,000 each, per family")
        self.assertEqual(dental[8], 5 * 2 * 3780, "twin, 10,000 each, per person")
        self.assertEqual(dental[16], 6048, "combined 10,000, per family")
        self.assertEqual(dental[24], 5 * 6048, "combined 10,000, per person")

        single = self.plan("ha_option_3", principal_age=35)
        group = self.options(single, "outpatient")[0]
        self.assertTrue(group["options"][0]["available"], "per person")
        self.assertFalse(group["options"][8]["available"], "shared: no member-only rate")
        older = self.plan("ha_option_1", principal_age=66)
        self.assertFalse(older["available"], "principal and spouse join from 18 to 65")

    def test_heriafya_per_person(self):
        """The tool's per-person basis: the spouse on the principal's rate for
        their own age group, children as on the family option."""
        family = {"principal_age": 35, "include_spouse": True, "spouse_age": 28, "children": 3}
        plan = self.plan("ha_option_3_pp", **family)
        self.assertTrue(plan["available"], plan["reason"])
        self.assertEqual(plan["core"]["amount"], 32400 + 21600 + 13500 + 2 * 10800)
        self.assertEqual(self.options(plan, "outpatient")[1][2], 42525 + 4 * 32400,
                         "the same options as the family plan")
        template = self.env.ref("odin_bizsure_quotes.cover_ha_option_3_pp").template_id
        self.assertEqual(template.odin_inpatient_limit, 1000000)
        self.assertEqual(template.odin_insurer_id, self.heritage)
        self.assertFalse(self.plan("ha_option_1_pp", principal_age=66)["available"])

    def test_afya_nafuu(self):
        """APA rates on age next birthday: entered as age today, each band a
        year lower. Outpatient and maternity: every tier with every plan."""
        Product = self.env["odin.cover.product"]
        plans = Product.search([("insurer_id", "=", self.apa.id), ("line", "=", "medical")])
        self.assertEqual(sorted(plans.mapped("name")), sorted(
            "APA Afya Nafuu – %s (KES %s)" % pair for pair in (
                ("Affluent", "1,000,000"), ("Executive", "750,000"), ("Standard", "500,000"),
                ("Select", "300,000"), ("Mwananchi", "100,000"))))
        for product in plans:
            self.assertFalse(product.warning_html, product.name)
            self.assertTrue(product.template_id.odin_medical, product.name)
            self.assertEqual(product.template_id.odin_insurer_id, self.apa, product.name)
        family = {"principal_age": 35, "include_spouse": True, "spouse_age": 28, "children": 3}
        plan = self.plan("an_standard", **family)
        self.assertTrue(plan["available"], plan["reason"])
        self.assertEqual(plan["core"]["amount"], 2 * 11643 + 3 * 6727,
                         "two adults on 21-40, three children on 1 month-20")
        self.assertEqual(self.options(plan, "outpatient")[1], [
            2 * 9100 + 3 * 6700, 2 * 11090 + 3 * 9000, 2 * 12900 + 3 * 11100,
            2 * 16500 + 3 * 13950, 2 * 20700 + 3 * 18200])
        self.assertEqual(self.options(plan, "maternity")[1], [7500, 10000, 15000])
        # 40 today is 41 next birthday: the 41-54 band.
        self.assertEqual(self.plan("an_affluent", principal_age=40)["core"]["amount"], 23499)
        self.assertEqual(self.plan("an_affluent", principal_age=39)["core"]["amount"], 16300)
        self.assertEqual(self.plan("an_mwananchi", principal_age=74)["core"]["amount"], 19890)
        self.assertFalse(self.plan("an_mwananchi", principal_age=75)["available"],
                         "last joining age 75 next birthday")

    def test_hadhi(self):
        result = self.Quote.search_plans({"cover": {
            "principal_age": 62, "include_spouse": True, "spouse_age": 58,
            "limit_min": 5000000, "limit_max": 5000000, "insurer": self.ga.name}})
        plans = {plan["name"]: plan for plan in result["plans"]}
        self.assertEqual(list(plans), ["Plan A (KES 5,000,000)"],
                         "the Covid-enhanced plans are off; Community Health is to 60")
        self.assertEqual(plans["Plan A (KES 5,000,000)"]["core"]["amount"], 185656,
                         "a couple, on the older member's age group (61-65)")
        self.assertEqual(self.plan("hadhi_d", principal_age=74)["core"]["amount"], 77280)
        too_old = self.plan("hadhi_a", principal_age=66)
        self.assertFalse(too_old["available"])
        self.assertEqual(too_old["reason"], "For families where everyone is under 66.")
        family = self.plan("hadhi_c", principal_age=70, include_spouse=True, spouse_age=68,
                           children=1)
        self.assertFalse(family["available"], "a member or a couple")
