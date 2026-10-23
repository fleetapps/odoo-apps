from odoo.tests import BaseCase, tagged

from ..models.rating import Household, age_fits, parse_rate, role_fits
from ..models.sale_order_template import distinguishing_labels


@tagged("post_install", "-at_install")
class TestRating(BaseCase):
    """How a product is priced, read from names exactly as the rate cards
    spell them -- en dashes, 'Day 1 to 20', 'Member + 4' and all."""

    def test_family_size(self):
        rate = parse_rate("Bizsure Afya Inpatient – Plan 1 (KES 200,000) – Member + 4 – Below 70")
        self.assertEqual(rate["basis"], "family_size")
        self.assertEqual(rate["family_size"], 5)
        self.assertEqual((rate["age_min"], rate["age_max"]), (0, 69))
        self.assertEqual(rate["limit"], 200000)
        self.assertEqual(rate["group"], "Bizsure Afya Inpatient – Plan 1 (KES 200,000) – Below 70")
        only = parse_rate("Bizsure Afya Inpatient – Plan 1 (KES 200,000) – Member Only – Below 70")
        self.assertEqual(only["family_size"], 1)
        self.assertEqual(only["group"], rate["group"])

    def test_outpatient_per_family_is_by_size(self):
        """'per Family' and 'Member + 2' together: the size wins."""
        rate = parse_rate("Bizsure Afya Outpatient – KES 50,000 per Family – Member + 2 – Above 70")
        self.assertEqual(rate["basis"], "family_size")
        self.assertEqual(rate["family_size"], 3)
        self.assertEqual((rate["age_min"], rate["age_max"]), (70, 0))
        self.assertEqual(rate["limit"], 50000)

    def test_role_and_band(self):
        rate = parse_rate("Jamii Plus Inpatient (Family) – KES 500,000 – Principal (21–40 Years)")
        self.assertEqual((rate["basis"], rate["role"]), ("person", "principal"))
        self.assertEqual((rate["age_min"], rate["age_max"]), (21, 40))
        self.assertEqual(rate["group"], "Jamii Plus Inpatient (Family) – KES 500,000")
        child = parse_rate("Jamii Plus Inpatient (Family) – KES 500,000 – Child (Day 1 to 20 Years)")
        self.assertEqual((child["role"], child["age_min"], child["age_max"]), ("child", 0, 20))
        self.assertEqual(child["group"], rate["group"])

    def test_per_person_band_any_role(self):
        rate = parse_rate("Jamii Plus Outpatient – KES 100,000 per Person (55–65 Years)")
        self.assertEqual((rate["basis"], rate["role"]), ("person", "any"))
        self.assertEqual((rate["age_min"], rate["age_max"]), (55, 65))
        self.assertEqual(rate["group"], "Jamii Plus Outpatient – KES 100,000 per Person")

    def test_child_only_is_part_of_the_product(self):
        rate = parse_rate("Jamii Plus Inpatient (Child Only) – KES 1,000,000")
        self.assertEqual((rate["basis"], rate["role"]), ("person", "child"))
        self.assertIn("(Child Only)", rate["group"])
        self.assertEqual(rate["limit"], 1000000)

    def test_main_member_and_dependants(self):
        main = parse_rate("Hosi Cash Rider – Option 1 (KES 30,000) – Main Member")
        each = parse_rate("Hosi Cash Rider – Option 1 (KES 30,000) – Each Dependant")
        self.assertEqual((main["role"], each["role"]), ("principal", "dependant"))
        self.assertEqual(main["group"], each["group"])
        self.assertEqual(main["group"], "Hosi Cash Rider – Option 1 (KES 30,000)")

    def test_once_per_policy(self):
        for name in ("Jamii Plus Maternity – KES 50,000 per Family", "Femina Plus Rider – Gold",
                     "Stamp Duty", "Critical Illness Rider – KES 250,000"):
            self.assertEqual(parse_rate(name)["basis"], "policy", name)
        self.assertEqual(parse_rate("Femina Plus Rider – Gold")["limit"], 0)

    def test_household(self):
        family = Household(35, 28, 3)
        self.assertEqual(family.headcount, 5)
        self.assertEqual(family.eldest, 35)
        self.assertEqual([m.role for m in family.members],
                         ["principal", "spouse", "child", "child", "child"])
        self.assertEqual(Household(children=2).principal, None)

    def test_fits(self):
        principal, spouse, child = Household(35, 28, 1).members
        self.assertTrue(role_fits("dependant", spouse))
        self.assertTrue(role_fits("dependant", child))
        self.assertFalse(role_fits("dependant", principal))
        self.assertTrue(role_fits("any", principal))
        self.assertTrue(age_fits(70, 0, 95), "no upper limit")
        self.assertFalse(age_fits(0, 69, 70))

    def test_labels(self):
        self.assertEqual(
            distinguishing_labels(["Femina Plus Rider – Silver", "Femina Plus Rider – Gold"]),
            ["Silver", "Gold"])
        self.assertEqual(
            distinguishing_labels(["PA Rider – Option A – Class (i)", "PA Rider – Option B – Class (i)"]),
            ["Option A – Class (i)", "Option B – Class (i)"])
        self.assertEqual(distinguishing_labels(["Only one"]), [""])
