from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MedicalQuoteCase


@tagged("post_install", "-at_install")
class TestEngine(MedicalQuoteCase):
    """Plans found and priced for the family on the screen."""

    def test_rate_fields_from_names(self):
        product = self.afya_ip[4].product_tmpl_id
        self.assertEqual(product.odin_rate_basis, "family_size")
        self.assertEqual(product.odin_rate_family_size, 5)
        self.assertEqual(self.jamii_p21.odin_rate_role, "principal")
        self.assertEqual(self.afya.odin_inpatient_limit, 200000)
        self.assertEqual(self.jamii.odin_inpatient_limit, 500000)
        self.assertEqual(self.jamii_children.odin_plan_type, "child_only")
        self.assertEqual(self.jamii_children.odin_inpatient_limit, 1000000,
                         "no limit on the section: read from the product")
        self.assertTrue(self.afya.odin_medical)

    def test_family_size_pricing(self):
        """Five people are Member + 4, on every per-family rate."""
        result = self.search()
        self.assertEqual(result["household"], {
            "headcount": 5, "principal": 35, "spouse": 28, "children": 3})
        afya = self.plan(result, self.afya)
        self.assertEqual(afya["core"]["amount"], 15822)
        self.assertEqual(afya["core"]["parts"][0]["who"], "Family of 5")
        outpatient = self.group(afya, "outpatient")
        self.assertEqual(len(outpatient["options"]), 1, "Member Only..+5 are one option")
        self.assertEqual(outpatient["options"][0]["amount"], 22130)
        self.assertEqual(outpatient["options"][0]["limit"], 50000)
        dental = self.group(afya, "dental")
        self.assertEqual([o["amount"] for o in dental["options"]], [6050, 9015], "5 × 1,210 and 5 × 1,803")
        self.assertEqual(dental["requires"], "outpatient")

    def test_per_person_pricing(self):
        jamii = self.plan(self.search(), self.jamii)
        self.assertEqual(jamii["core"]["amount"], 21286 + 17289 + 3 * 9033)
        parts = {(p["who"], p["qty"], p["price"]) for p in jamii["core"]["parts"]}
        self.assertEqual(parts, {("Principal", 1, 21286), ("Spouse", 1, 17289), ("Children", 3, 9033)})
        outpatient = self.group(jamii, "outpatient")["options"][0]
        self.assertEqual(outpatient["amount"], 2 * 34079 + 3 * 28398)
        riders = [g for g in jamii["groups"] if g["benefit_key"] == "riders"]
        self.assertEqual([g["label"] for g in riders], ["Femina Plus", "Hosi Cash"])
        self.assertEqual([o["label"] for o in riders[0]["options"]], ["Silver", "Gold"])
        self.assertEqual(riders[1]["options"][0]["amount"], 600 + 4 * 300, "main member + 4 dependants")

    def test_band_follows_the_age(self):
        """A 45-year-old principal moves to the 41-54 rows, the spouse stays."""
        jamii = self.plan(self.search(principal_dob=self.dob(45)), self.jamii)
        self.assertEqual(jamii["core"]["amount"], 27775 + 17289 + 3 * 9033)
        outpatient = self.group(jamii, "outpatient")["options"][0]
        self.assertEqual(outpatient["amount"], 40894 + 34079 + 3 * 28398)

    def test_no_rate_is_said_not_guessed(self):
        result = self.search(principal_dob=self.dob(60))
        refused = {p["id"]: p["reason"] for p in result["unavailable"]}
        self.assertIn(self.jamii.id, refused)
        self.assertIn("principal", refused[self.jamii.id])
        self.assertIn("60", refused[self.jamii.id])

    def test_over_and_under_seventy(self):
        young = self.search()
        self.assertIn(self.afya.id, [p["id"] for p in young["plans"]])
        self.assertIn("70 or over", next(
            p["reason"] for p in young["unavailable"] if p["id"] == self.afya70.id))
        old = self.search(principal_dob=self.dob(72), include_spouse=False, children=0)
        self.assertIn(self.afya70.id, [p["id"] for p in old["plans"]])
        self.assertEqual(self.plan(old, self.afya70)["core"]["amount"], 9793)
        self.assertIn("under 70", next(
            p["reason"] for p in old["unavailable"] if p["id"] == self.afya.id))

    def test_family_too_large(self):
        result = self.search(children=6)
        reason = next(p["reason"] for p in result["unavailable"] if p["id"] == self.afya.id)
        self.assertIn("up to 6", reason)

    def test_limit_range_and_insurer(self):
        ids = [p["id"] for p in self.search(limit_min=300000, limit_max=600000)["plans"]]
        self.assertEqual(ids, [self.jamii.id])
        ids = [p["id"] for p in self.search(insurer="Test Afya Insurer")["plans"]]
        self.assertEqual(ids, [self.afya.id])
        ids = [p["id"] for p in self.search(template_id=self.jamii.id, limit_max=1)["plans"]]
        self.assertEqual(ids, [self.jamii.id], "a plan picked by name ignores the range")
        with self.assertRaises(UserError):
            self.search(limit_min=500000, limit_max=100000)

    def test_children_only(self):
        result = self.Quote.search_plans({"cover": {"children_only": True, "children": 2}})
        self.assertEqual([p["id"] for p in result["plans"]], [self.jamii_children.id])
        self.assertFalse(result["unavailable"], "family plans are not offered, not refused")
        self.assertEqual(result["plans"][0]["core"]["amount"], 2 * 21281)

    def test_cover_is_checked(self):
        with self.assertRaises(UserError):
            self.Quote.search_plans({"cover": {"children": 2}})
        with self.assertRaises(UserError):
            self.search(spouse_dob=False)
        with self.assertRaises(UserError):
            self.search(principal_dob="2999-01-01")
        with self.assertRaises(UserError):
            self.Quote.search_plans({"cover": {"children_only": True, "children": 0}})
