from odoo import Command
from odoo.tests import tagged

from .common import MedicalQuoteCase


def priced(result):
    """Every plan's core premium and each option's price, by key."""
    return {
        plan["id"]: (plan["core"]["amount"], {
            option["key"]: option["amount"]
            for group in plan["groups"] for option in group["options"]})
        for plan in result["plans"]
    }


@tagged("post_install", "-at_install")
class TestMedicalProducts(MedicalQuoteCase):
    """Plans as products: adopting the catalogue changes no price, a rate
    changed in its table is the price quoted, a new plan made in the form is
    quoted, and a copy never changes its original."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Product = cls.env["odin.cover.product"]
        cls.before = priced(cls.Quote.search_plans({"cover": {
            "principal_age": 35, "include_spouse": True, "spouse_age": 28, "children": 3}}))
        cls.Product._odin_adopt_templates()

    def product(self, template):
        return self.Product.search([("template_id", "=", template.id)])

    def family(self, **values):
        return dict({"principal_age": 35, "include_spouse": True, "spouse_age": 28,
                     "children": 3}, **values)

    def test_adopted_prices_unchanged(self):
        after = priced(self.Quote.search_plans({"cover": self.family()}))
        self.assertEqual(after, self.before, "adopting the catalogue changes no premium or option")
        jamii = self.product(self.jamii)
        self.assertEqual(jamii.insurer_id, self.insurer_jamii)
        self.assertEqual(set(jamii.table_ids.mapped("cover")), {"inpatient", "outpatient", "other"})
        inpatient = jamii.table_ids.filtered(lambda t: t.cover == "inpatient")
        self.assertEqual(inpatient.basis, "person")
        self.assertIn(self.jamii_p41.product_tmpl_id, inpatient.row_ids,
                      "every row of the rate group, not only those on the template")
        self.assertEqual(self.jamii_p21.name,
                         "Jamii Inpatient (Family) – KES 500,000 – Principal (21–40 Years)",
                         "adopting renames nothing")
        self.Product._odin_adopt_templates()
        self.assertEqual(len(self.product(self.jamii)), 1, "adopting twice makes nothing twice")

    def test_shared_table(self):
        afya = self.product(self.afya)
        afya70 = self.product(self.afya70)
        self.assertFalse(afya.table_ids & afya70.table_ids, "below and above 70 are separate tables")
        outpatient = afya.table_ids.filtered(lambda t: t.cover == "outpatient")
        self.assertEqual(outpatient.basis, "family_size")
        self.assertEqual(sorted(outpatient.row_ids.mapped("odin_row_family_size")), [1, 2, 3, 4, 5, 6])

    def test_rate_change_is_quoted(self):
        jamii = self.product(self.jamii)
        inpatient = jamii.table_ids.filtered(lambda t: t.cover == "inpatient")
        row = self.jamii_p21.product_tmpl_id
        self.assertIn(row, inpatient.row_ids)
        row.list_price = 22000
        plan = next(p for p in self.Quote.search_plans({"cover": self.family()})["plans"]
                    if p["id"] == self.jamii.id)
        self.assertEqual(plan["core"]["amount"], 22000 + 17289 + 3 * 9033)
        self.assertIn("21,286", jamii.message_ids[0].body, "the change is logged on the product")

    def test_new_age_band_row(self):
        jamii = self.product(self.jamii)
        inpatient = jamii.table_ids.filtered(lambda t: t.cover == "inpatient")
        inpatient.row_ids = [Command.create({
            "odin_row_role": "principal", "odin_row_age_min": 55, "odin_row_age_max": 64,
            "list_price": 35000})]
        new = inpatient.row_ids.filtered(lambda r: r.odin_row_age_min == 55)
        self.assertEqual(new.odin_rate_group, inpatient.rate_group)
        self.assertEqual(new.odin_rate_role, "principal")
        self.assertEqual(new.type, "service")
        self.assertEqual(new.taxes_id, self.levies, "levies as on the table's other rows")
        self.assertIn("Principal, aged 55–64", new.name)
        plan = next(p for p in self.Quote.search_plans({"cover": self.family(principal_age=60)})["plans"]
                    if p["id"] == self.jamii.id)
        self.assertEqual(plan["core"]["parts"][0]["price"], 35000)

    def test_removed_row_archived(self):
        afya = self.product(self.afya)
        dental = afya.table_ids.filtered(lambda t: t.cover == "dental")
        row = dental.row_ids[:1]
        dental.row_ids = [Command.delete(row.id)]
        self.assertTrue(row.exists(), "kept for the quotes that used it")
        self.assertFalse(row.active)

    def test_new_plan_from_the_form(self):
        self.env["sale.order.template"].search([("odin_medical", "=", True)]).number_of_days = 30
        product = self.Product.create({
            "name": "Test Insurer – Family KES 300,000",
            "line": "medical",
            "insurer_id": self.insurer_afya.id,
            "plan_type": "family",
            "table_ids": [
                Command.create({
                    "name": "Test Inpatient – KES 300,000",
                    "cover": "inpatient", "basis": "family_size", "limit": 300000,
                    "row_ids": [Command.create({"odin_row_family_size": n, "list_price": price})
                                for n, price in ((1, 10000), (2, 15000), (3, 19000),
                                                 (4, 23000), (5, 27000))],
                }),
                Command.create({
                    "name": "Test Outpatient – KES 50,000",
                    "cover": "outpatient", "basis": "family_size", "limit": 50000,
                    "row_ids": [Command.create({"odin_row_family_size": n, "list_price": 5000 * n})
                                for n in range(1, 6)],
                }),
            ],
        })
        template = product.template_id
        self.assertTrue(template, "a template is made")
        self.assertTrue(template.odin_medical)
        self.assertEqual(template.odin_inpatient_limit, 300000)
        self.assertEqual(template.odin_insurer_id, self.insurer_afya)
        ordered = template.sale_order_template_line_ids.sorted(lambda l: (l.sequence, l.id))
        keys = [line.benefit_key for line in ordered if line.display_type == "line_section"]
        self.assertEqual(keys[:2], ["inpatient", "outpatient"])
        self.assertIn("statutory_charges", keys, "charges copied from the latest plan")
        self.assertEqual(template.number_of_days, 30, "valid as long as the other plans")
        result = self.Quote.search_plans({"cover": self.family()})
        plan = next(p for p in result["plans"] if p["id"] == template.id)
        self.assertEqual(plan["core"]["amount"], 27000, "a family of five: Member + 4")
        outpatient = self.group(plan, "outpatient")
        self.assertEqual(outpatient["options"][0]["amount"], 25000)
        self.assertFalse(product.warning_html)

        product.table_ids = [Command.unlink(product.table_ids.filtered(
            lambda t: t.cover == "outpatient").id)]
        template.invalidate_recordset(["sale_order_template_line_ids"])
        self.assertNotIn("outpatient", [line.benefit_key for line in
                                        template.sale_order_template_line_ids
                                        if line.display_type == "line_section"])

    def test_warnings(self):
        product = self.Product.create({
            "name": "Gappy plan", "line": "medical", "insurer_id": self.insurer_afya.id,
            "table_ids": [Command.create({
                "name": "Gappy Inpatient", "cover": "inpatient", "basis": "person",
                "row_ids": [
                    Command.create({"odin_row_role": "principal", "odin_row_age_min": 18,
                                    "odin_row_age_max": 40, "list_price": 100}),
                    Command.create({"odin_row_role": "principal", "odin_row_age_min": 46,
                                    "odin_row_age_max": 60, "list_price": 0}),
                ]})]})
        self.assertIn("aged 41 to 45", product.warning_html)
        self.assertIn("no premium", product.warning_html)

    def test_copy_is_independent(self):
        afya = self.product(self.afya)
        copy = afya.copy()
        self.assertFalse(copy.active, "a copy starts off: never quoted twice by accident")
        self.assertFalse(copy.template_id.active)
        copy.active = True
        self.assertTrue(copy.template_id.active, "switched on, it is quoted")
        self.assertNotEqual(copy.template_id, afya.template_id)
        self.assertFalse(copy.table_ids & afya.table_ids)
        row = copy.table_ids.filtered(lambda t: t.cover == "inpatient").row_ids.filtered(
            lambda r: r.odin_row_family_size == 5)
        row.list_price = 1
        result = priced(self.Quote.search_plans({"cover": self.family()}))
        self.assertEqual(result[self.afya.id], self.before[self.afya.id], "the original is untouched")
        self.assertEqual(result[copy.template_id.id][0], 1)

    def test_archive(self):
        afya = self.product(self.afya)
        afya.action_archive()
        self.assertFalse(afya.template_id.active)
        ids = [p["id"] for p in self.Quote.search_plans({"cover": self.family()})["plans"]]
        self.assertNotIn(self.afya.id, ids)

    def test_try_it(self):
        jamii = self.product(self.jamii)
        jamii.write({"try_principal_age": 35, "try_spouse_age": 28, "try_children": 3})
        self.assertIn("21,286", jamii.try_html)
        self.assertIn("Hosi Cash", jamii.try_html)

    def _eldest_plan(self):
        """J Care's shape: inpatient and outpatient rated on the eldest's age,
        a child rate on inpatient only; personal accident for adult
        dependants and last expense for dependants, the principal's free."""
        def rows(*values):
            return [Command.create(dict(zip(
                ("odin_row_role", "odin_row_age_min", "odin_row_age_max", "list_price"), row)))
                for row in values]
        product = self.Product.create({
            "name": "Eldest – Classic (KES 500,000)",
            "line": "medical",
            "insurer_id": self.insurer_jamii.id,
            "table_ids": [
                Command.create({
                    "name": "Eldest Inpatient – KES 500,000", "cover": "inpatient",
                    "basis": "person", "age_basis": "eldest", "limit": 500000,
                    "row_ids": rows(("principal", 18, 30, 21316), ("spouse", 18, 30, 15673),
                                    ("principal", 31, 40, 22569), ("spouse", 31, 40, 18808),
                                    ("principal", 41, 50, 23215), ("spouse", 41, 50, 19453),
                                    ("child", 0, 17, 10282)),
                }),
                Command.create({
                    "name": "Eldest Outpatient – KES 50,000 per Person", "cover": "outpatient",
                    "basis": "person", "age_basis": "eldest", "limit": 50000,
                    "row_ids": rows(("any", 0, 40, 26555), ("any", 41, 50, 30315)),
                }),
                Command.create({
                    "name": "Eldest Personal Accident – KES 500,000", "cover": "other",
                    "cover_label": "Personal accident", "basis": "person", "partial": True,
                    "limit": 500000, "row_ids": rows(("dependant", 18, 0, 586)),
                }),
                Command.create({
                    "name": "Eldest Last Expense – KES 50,000", "cover": "other",
                    "cover_label": "Last expense", "basis": "person", "partial": True,
                    "limit": 50000, "row_ids": rows(("dependant", 0, 0, 1007)),
                }),
            ],
        })
        return product

    def eldest_quote(self, product, **cover):
        result = self.Quote.search_plans({"cover": dict(
            {"principal_age": 35, "include_spouse": True, "spouse_age": 42, "children": 2,
             "template_id": product.template_id.id}, **cover)})
        return (result["plans"] or result["unavailable"])[0]

    def test_rated_on_the_eldest(self):
        """"Premium will be based on the age of the oldest applicant": a
        principal of 35 with a spouse of 42 are both on the 41-50 rates."""
        product = self._eldest_plan()
        plan = self.eldest_quote(product)
        self.assertTrue(plan["available"], plan["reason"])
        self.assertEqual(plan["core"]["amount"], 23215 + 19453 + 2 * 10282,
                         "both adults at 41-50, each child on the child rate")
        outpatient = self.group(plan, "outpatient")["options"][0]
        self.assertEqual(outpatient["amount"], 4 * 30315,
                         "no child rate on outpatient: everyone at the eldest's band")
        younger = self.eldest_quote(product, spouse_age=28)
        self.assertEqual(younger["core"]["amount"], 22569 + 18808 + 2 * 10282,
                         "the principal of 35 is the eldest: 31-40")
        too_old = self.eldest_quote(product, spouse_age=55)
        self.assertFalse(too_old["available"])
        self.assertIn("eldest is 55", too_old["reason"])
        inpatient = product.table_ids.filtered(lambda t: t.cover == "inpatient")
        names = inpatient.row_ids.mapped("name")
        self.assertIn("Eldest Inpatient – KES 500,000 – Spouse, eldest aged 41–50", names)
        self.assertIn("Eldest Inpatient – KES 500,000 – Child, aged 0–17", names,
                      "a child rate is the child's own age")
        self.assertFalse(product.warning_html)

    def test_only_some_people(self):
        """Personal accident only for adult dependants, last expense only for
        dependants: the others are left off, not the cover refused."""
        product = self._eldest_plan()
        plan = self.eldest_quote(product)
        accident = self.group(plan, "personal_accident")["options"][0]
        self.assertTrue(accident["available"], accident["reason"])
        self.assertEqual(accident["amount"], 586, "the spouse only")
        self.assertEqual(accident["parts"][0]["who"], "Spouse")
        expense = self.group(plan, "last_expense")["options"][0]
        self.assertEqual(expense["amount"], 3 * 1007, "the spouse and two children")
        single = self.eldest_quote(product, include_spouse=False)
        accident = self.group(single, "personal_accident")["options"][0]
        self.assertFalse(accident["available"], "nobody to cover: not offered at 0")
