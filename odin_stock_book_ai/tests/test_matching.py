"""The matching rules on their own, on the names the store's people mix up."""

from odoo.tests import TransactionCase, tagged

from ..models import matching

CATALOGUE = [
    {"id": 1, "code": "FIN-PAK-040", "name": "Avocado & Grapeseed Shampoo 500 Ml", "category": "Finished Goods / Hair",
     "uom": "Units"},
    {"id": 2, "code": "FIN-PAK-117", "name": "Avocado & Grapeseed Conditioner 500 Ml",
     "category": "Finished Goods / Hair", "uom": "Units"},
    {"id": 3, "code": "FIN-PAK-044", "name": "Coconut & Aloe Shampoo 500 Ml", "category": "Finished Goods / Hair",
     "uom": "Units"},
    {"id": 4, "code": "FIN-PAK-119", "name": "Coconut & Aloe Conditioner 500 Ml", "category": "Finished Goods / Hair",
     "uom": "Units"},
    {"id": 5, "code": "FIN-PAK-061", "name": "Layla Baby Shampoo 500 Ml", "category": "Finished Goods / Baby",
     "uom": "Units"},
    {"id": 6, "code": "FIN-PAK-062", "name": "Layla Baby Lotion 500 Ml", "category": "Finished Goods / Baby",
     "uom": "Units"},
    {"id": 7, "code": "FIN-BULK-020", "name": "Multipurpose Regular (Bulk)", "category": "Finished Goods / Bulk",
     "uom": "L"},
    {"id": 8, "code": "FIN-PAK-030", "name": "Multipurpose Reg 1000 Ml", "category": "Finished Goods / Cleaning",
     "uom": "Units"},
    {"id": 9, "code": "FIN-PAK-031", "name": "Multipurpose Reg 500 Ml", "category": "Finished Goods / Cleaning",
     "uom": "Units"},
    {"id": 10, "code": "FIN-PAK-032", "name": "Multipurpose Reg 5 Litre", "category": "Finished Goods / Cleaning",
     "uom": "Units"},
    {"id": 11, "code": "FIN-PAK-063", "name": "Layla Baby Shampoo 250 Ml", "category": "Finished Goods / Baby",
     "uom": "Units"},
]


@tagged("post_install", "-at_install")
class TestMatching(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.catalogue = matching.Catalogue(CATALOGUE)

    def best(self, description, **kwargs):
        found = self.catalogue.match(description, **kwargs)
        return (found["best"]["product"]["id"] if found["best"] else None), found

    def test_a_shampoo_is_never_the_conditioner_of_its_range(self):
        best, found = self.best("HAIR SHAMPOO AVOCADO & GRAPESEED 500 ML")
        self.assertEqual((best, found["confidence"]), (1, matching.SURE))
        self.assertEqual([c["product"]["id"] for c in found["candidates"] if c["product"]["id"] == 2], [],
                         "the conditioner is not even a candidate")
        self.assertIn("a conditioner; the invoice says shampoo", found["excluded"][0]["why"],
                      "the near-miss is said, with why")
        self.assertEqual(self.best("AVOCADO GRAPESEED COND 500ML")[0], 2, "COND is the conditioner")
        self.assertEqual(self.best("COCONUT ALOE SHAMPOO 500ML")[0], 3)
        self.assertEqual(self.best("COCONUT & ALOE CONDITIONER 500 ML")[0], 4)

    def test_the_baby_lotion_is_not_the_baby_shampoo(self):
        self.assertEqual(self.best("LAYLA BABY SHAMPOO 500 ML")[0], 5)
        self.assertEqual(self.best("LAYLA BABY LOTION 500ML")[0], 6)

    def test_the_size_decides_and_another_size_is_never_matched(self):
        best, found = self.best("MULTIPURPOSE REG 1000 ml")
        self.assertEqual((best, found["confidence"]), (8, matching.SURE))
        self.assertEqual(self.best("MULTIPURPOSE REG 1 LTR")[0], 8, "1 litre is 1000 ml")
        self.assertEqual(self.best("Multi Purpose Regular 5Ltr")[0], 10)
        self.assertEqual(self.best("LAYLA BABY SHAMPOO 250ML")[0], 11)
        excluded = {c["product"]["id"] for c in found["excluded"]}
        self.assertTrue({9, 10} <= excluded, "500 ml and 5 litre are ruled out")

    def test_no_size_on_the_invoice_leaves_the_sizes_to_choose(self):
        best, found = self.best("LAYLA BABY SHAMPOO")
        self.assertEqual(found["confidence"], matching.CHECK, "500 ml or 250 ml: not guessed")
        self.assertEqual({c["product"]["id"] for c in [found["best"]] + found["close"]}, {5, 11})

    def test_a_bulk_item_only_when_the_invoice_says_bulk(self):
        best, found = self.best("MULTIPURPOSE REGULAR")
        self.assertNotIn(7, [c["product"]["id"] for c in found["candidates"]], "never bulk unless it says so")
        self.assertIn("a bulk item; the invoice does not say bulk",
                      next(c["why"] for c in found["excluded"] if c["product"]["id"] == 7))
        best, found = self.best("MULTIPURPOSE REGULAR BULK 20 LTRS")
        self.assertEqual((best, found["confidence"]), (7, matching.SURE))

    def test_the_code_wins_but_a_conflict_with_it_is_said(self):
        best, found = self.best("AVOCADO SHAMPOO 500ML", code="fin-pak-040")
        self.assertEqual((best, found["confidence"]), (1, matching.SURE))
        best, found = self.best("FIN-PAK-117 AVOCADO SHAMPOO 500ML")
        self.assertEqual(best, 2, "the code printed is the conditioner's")
        self.assertEqual(found["confidence"], matching.CHECK, "but the words say shampoo: checked, not trusted")

    def test_the_customers_price_breaks_a_tie(self):
        _best, found = self.best("LAYLA BABY SHAMPOO", rate=180.0, prices={5: 350.0, 11: 180.0})
        self.assertEqual(found["best"]["product"]["id"], 11, "the 250 ml is the one at its price")

    def test_nothing_matches_says_none(self):
        best, found = self.best("HAIR FOOD 250G")
        self.assertEqual((best, found["confidence"]), (None, matching.NONE))

    def test_sizes_as_invoices_write_them(self):
        self.assertEqual(matching.sizes("Type G - Hand Wash 500Mlx12"), {("volume", 500.0)})
        self.assertEqual(matching.sizes("1,000 ML"), {("volume", 1000.0)})
        self.assertEqual(matching.sizes("1.5L"), {("volume", 1500.0)})
        self.assertEqual(matching.sizes("5 Litre"), {("volume", 5000.0)})
        self.assertEqual(matching.sizes("PETROLEUM JELLY 250G"), {("mass", 250.0)})
        self.assertEqual(matching.sizes("1 lemon"), set())

    def test_a_branch_is_a_contact_under_the_customer(self):
        partners = [
            {"id": 1, "name": "Chandarana Supermarket", "parent_id": False, "vat": "P051111111A", "children": 2},
            {"id": 2, "name": "Hookwood Square - Brookside Grove", "parent_id": 1, "vat": "P051111111A",
             "children": 0},
            {"id": 3, "name": "Ngara", "parent_id": 1, "vat": "P051111111A", "children": 0},
            {"id": 4, "name": "Chandarana Foodplus", "parent_id": False, "vat": "", "children": 0},
        ]
        found = matching.match_customer("CHANDARANA SUPERMARKET", "HOOKWOOD SQUARE - BROOKSIDE GROVE", "", partners)
        self.assertEqual((found["best"]["partner"]["id"], found["confidence"]), (2, matching.SURE))
        found = matching.match_customer("CHANDARANA SUPERMARKET BROOKSIDE GROVE", "", "", partners)
        self.assertEqual(found["best"]["partner"]["id"], 2, "the branch run into the name")
        found = matching.match_customer("CHANDARANA SUPERMARKET", "WESTLANDS", "", partners)
        self.assertEqual(found["confidence"], matching.CHECK, "a branch that is not a contact: picked by hand")
        found = matching.match_customer("CHANDARANA SUPERMARKET", "", "", partners)
        self.assertEqual((found["best"]["partner"]["id"], found["confidence"]), (1, matching.SURE))
        found = matching.match_customer("SOMEONE ELSE", "", "", partners)
        self.assertEqual(found["confidence"], matching.NONE)
