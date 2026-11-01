from odoo.tests import TransactionCase, tagged
from odoo.tools import html2plaintext

from odoo.addons.odin_medical_quote.models.quote_comparison import SHORT_TERMS

#: What every plan's key terms says, in this order.
SECTIONS = ("Your premium.", "Who can join.", "Waiting periods.", "Not covered.", "How to pay.",
            "Please note.")


@tagged("post_install", "-at_install")
class TestKeyTerms(TransactionCase):
    """Every plan prints its key terms: about half a page, the same sections
    in the same order, and the clauses that protect Bizsure."""

    def plans(self):
        """Every medical plan this module installs, the ones that are off too."""
        Product = self.env["odin.cover.product"].with_context(active_test=False)
        templates = self.env["sale.order.template"].browse(self.env["ir.model.data"].search([
            ("module", "=", "odin_bizsure_quotes"), ("model", "=", "sale.order.template"),
        ]).mapped("res_id"))
        products = Product.browse(self.env["ir.model.data"].search([
            ("module", "=", "odin_bizsure_quotes"), ("model", "=", "odin.cover.product"),
        ]).mapped("res_id"))
        return products | Product.search([("template_id", "in", templates.ids)])

    def test_every_plan(self):
        plans = self.plans()
        self.assertEqual(len(plans), 16 + 61, "the catalogue's 16 plans and the 61 added")
        for plan in plans:
            text = html2plaintext(plan.terms)
            self.assertLess(len(text), SHORT_TERMS, "%s: about half a page" % plan.name)
            positions = [text.find(section) for section in SECTIONS]
            self.assertNotIn(-1, positions, "%s: %s" % (plan.name, text))
            self.assertEqual(positions, sorted(positions), "%s: in order" % plan.name)
            self.assertIn("This is a quotation, not cover", text, plan.name)
            self.assertIn("Declare all health conditions", text, plan.name)
            self.assertIn("policy document prevails", text, plan.name)
            self.assertEqual(html2plaintext(plan.template_id.note), text,
                             "%s: the quotation prints what the product says" % plan.name)
            for stale in ("90 days from day of issue", "Caroline Chege", "valid for 30 days"):
                self.assertNotIn(stale, text, plan.name)

    def test_three_insurers_on_one_quote(self):
        """APA, Jubilee and Heritage on one quotation: each insurer's key
        terms once, under its product's name, each kept whole on its page."""
        Quote = self.env["odin.medical.quote"]
        names = ("APA Jamii Plus – Family Cover (KES 1,000,000)",
                 "Jubilee J Care – Premier (KES 1,000,000)",
                 "Heritage HeriAfya – Option 3 (KES 1,000,000)")
        templates = [self.env["sale.order.template"].search([("name", "=", name)]) for name in names]
        saved = Quote.save_quote({
            "customer": {"source": "new", "name": "Wanjiku Kamau", "phone": "0722 111 333"},
            "cover": {"principal_age": 35, "include_spouse": True, "spouse_age": 32,
                      "children": 2},
            "plans": [{"template_id": template.id, "options": []} for template in templates],
        })
        comparison = self.env["odin.quote.comparison"].browse(saved["comparison"]["id"])
        terms = comparison._odin_export_terms()
        self.assertEqual([term["title"] for term in terms],
                         ["APA Jamii Plus", "Jubilee J Care", "Heritage HeriAfya"])
        self.assertTrue(all(term["short"] for term in terms), "each kept whole on its page")
        self.assertLess(sum(len(html2plaintext(term["note"])) for term in terms), 3 * SHORT_TERMS)
