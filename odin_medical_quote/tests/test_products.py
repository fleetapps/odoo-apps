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
