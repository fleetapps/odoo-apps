from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestQuoteExample(TransactionCase):
    """The worked example of the brief, from these rate cards.

    Principal 35, spouse 28, three children -- five people, so Member + 4 --
    inpatient limits from 200,000 to 750,000, Birdview:

    ============  =========  ==========  ======  =======  ======  ======
    Plan          Inpatient  Outpatient  Dental  Optical  Levies  Total
    ============  =========  ==========  ======  =======  ======  ======
    Plan 1        15,822     22,130      6,050   6,050    265     50,317
    Plan 2        22,713     28,823      9,015   9,015    353     69,919
    Plan 3        28,559     31,798      12,040  12,040   420     84,857
    ============  =========  ==========  ======  =======  ======  ======

    "Levies" is the 0.45% training levy and PHCF plus KES 40 stamp duty, as
    the example shows them in one line.
    """

    def dob(self, age):
        today = fields.Date.context_today(self.env["odin.medical.quote"])
        return fields.Date.to_string(today - relativedelta(years=age, months=3))

    def test_pdf_example(self):
        Quote = self.env["odin.medical.quote"]
        cover = {
            "principal_dob": self.dob(35),
            "include_spouse": True,
            "spouse_dob": self.dob(28),
            "children": 3,
            "limit_min": 200000,
            "limit_max": 750000,
            "insurer": self.env.ref("odin_bizsure_quotes.insurer_birdview").name,
        }
        result = Quote.search_plans({"cover": cover})
        plans = {plan["name"]: plan for plan in result["plans"]}
        self.assertEqual(sorted(plans), [
            "Plan 1 (KES 200,000) – Below 70",
            "Plan 2 (KES 300,000) – Below 70",
            "Plan 3 (KES 500,000) – Below 70",
            "Plan 4 (KES 750,000) – Below 70",
        ])
        expected = {
            "Plan 1 (KES 200,000) – Below 70": (15822, 22130, 5000, 6050, 265, 50317),
            "Plan 2 (KES 300,000) – Below 70": (22713, 28823, 7500, 9015, 353, 69919),
            "Plan 3 (KES 500,000) – Below 70": (28559, 31798, 10000, 12040, 420, 84857),
        }
        selections = []
        for name, (inpatient, outpatient, tier, per_family, _levies, _total) in expected.items():
            plan = plans[name]
            self.assertEqual(plan["core"]["amount"], inpatient, name)
            groups = {group["benefit_key"]: group for group in plan["groups"]}
            self.assertEqual(groups["outpatient"]["options"][0]["amount"], outpatient, name)
            options = [groups["outpatient"]["options"][0]["key"]]
            for benefit in ("dental", "optical"):
                option = next(o for o in groups[benefit]["options"] if o["limit"] == tier)
                self.assertEqual(option["amount"], per_family, "%s %s" % (name, benefit))
                options.append(option["key"])
            selections.append({"template_id": plan["id"], "options": options})

        totals = Quote.compute_totals({"cover": cover, "plans": selections})
        for selection, (name, values) in zip(selections, expected.items()):
            plan_totals = totals[str(selection["template_id"])]
            self.assertEqual(round(plan_totals["levies"]), values[4], name)
            self.assertEqual(round(plan_totals["total"]), values[5], name)

    def test_insurers_and_outpatient_tiers(self):
        apa = self.env.ref("odin_bizsure_quotes.sot_apa_jamii_plus_family_cover_kes_500_000")
        self.assertEqual(apa.odin_insurer_id, self.env.ref("odin_bizsure_quotes.insurer_apa"))
        self.assertEqual(apa.odin_inpatient_limit, 500000)
        result = self.env["odin.medical.quote"].search_plans({"cover": {
            "principal_dob": self.dob(45), "children": 1, "template_id": apa.id}})
        outpatient = next(g for g in result["plans"][0]["groups"] if g["benefit_key"] == "outpatient")
        self.assertEqual([o["limit"] for o in outpatient["options"]],
                         [50000, 75000, 100000, 150000, 200000])
        # 41-54 band for the principal, the child band for the child.
        self.assertEqual(outpatient["options"][0]["amount"], 28981 + 20040)
