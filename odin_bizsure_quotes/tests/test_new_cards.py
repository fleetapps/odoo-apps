from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestNewCards(TransactionCase):
    """GA Community Health, Jubilee J Care, J Care Junior and J Senior as
    New quote prices them, each figure read off the insurer's own card.

    Jubilee rates the family on the age of the oldest applicant: a principal
    of 35 with a spouse of 42 are both on the 41-50 rates.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Quote = cls.env["odin.medical.quote"]
        cls.jubilee = cls.env.ref("odin_bizsure_quotes.insurer_jubilee")
        cls.ga = cls.env.ref("odin_bizsure_quotes.insurer_ga")

    def plan(self, key, **cover):
        product = self.env.ref("odin_bizsure_quotes.cover_%s" % key)
        result = self.Quote.search_plans({"cover": dict(cover, template_id=product.template_id.id)})
        return (result["plans"] + result["unavailable"])[0]

    @staticmethod
    def options(plan, benefit):
        group = next(g for g in plan["groups"] if g["benefit_key"] == benefit)
        return group, [option["amount"] for option in group["options"]]

    def test_plans_installed(self):
        products = self.env["odin.cover.product"].search(
            [("insurer_id", "in", (self.jubilee | self.ga).ids)])
        self.assertEqual(len(products.filtered(lambda p: p.insurer_id == self.ga)), 26)
        self.assertEqual(len(products.filtered(lambda p: p.insurer_id == self.jubilee)), 17)
        birdview = self.env.ref(
            "odin_bizsure_quotes.sot_birdview_bizsure_afya_plan_1_kes_200_000_below_70")
        for product in products:
            template = product.template_id
            self.assertTrue(template.odin_medical, product.name)
            self.assertEqual(template.odin_insurer_id, product.insurer_id, product.name)
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
        self.assertEqual(sorted(plans), ["KES 1,000,000 – Excluding Pandemic",
                                         "KES 1,000,000 – Including Pandemic"])
        self.assertEqual(plans["KES 1,000,000 – Excluding Pandemic"]["core"]["amount"], 72915)
        self.assertEqual(plans["KES 1,000,000 – Including Pandemic"]["core"]["amount"], 87498)
        self.assertEqual(self.options(plans["KES 1,000,000 – Excluding Pandemic"], "maternity")[1],
                         [13283, 17078, 18975, 20873, 22770, 34085])
        older = self.plan("ga_250k_excl", principal_age=61)
        self.assertFalse(older["available"], "last joining age 60")
        large = self.plan("ga_10m_incl", principal_age=50, include_spouse=True, spouse_age=45,
                          children=5)
        self.assertEqual(large["core"]["amount"], 236437, "M + 6")
