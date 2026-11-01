from odoo import Command
from odoo.tests import tagged
from odoo.tools import format_date, html2plaintext

from .common import MedicalQuoteCase


@tagged("post_install", "-at_install")
class TestExport(MedicalQuoteCase):
    """The medical quotation: what Print, Send by email and WhatsApp send."""

    def setUp(self):
        super().setUp()
        result = self.search(limit_min=200000, limit_max=500000)
        afya = self.plan(result, self.afya)
        saved = self.Quote.save_quote({
            "customer": {"source": "new", "name": "David Muthuri Muriuki",
                         "phone": "0722537502", "email": "dmuthuri@example.com"},
            "cover": self.cover(limit_min=200000, limit_max=500000),
            "plans": [
                {"template_id": self.afya.id, "discount": "10%",
                 "options": [self.option(afya, "outpatient"), self.option(afya, "dental")]},
                {"template_id": self.jamii.id, "options": [], "recommended": True,
                 "policy_charge": "500"},
            ],
        })
        self.comparison = self.env["odin.quote.comparison"].with_user(self.agent).browse(
            saved["comparison"]["id"])

    def html(self):
        html, _type = self.env["ir.actions.report"].with_user(self.agent)._render_qweb_html(
            "odin_medical_quote.report_medical_quotation", self.comparison.ids)
        return html2plaintext(html.decode())

    def test_members_as_entered(self):
        members = dict(self.comparison._odin_export_members())
        cover = self.comparison.odin_builder_params["cover"]
        self.assertEqual(members["Principal DOB"], format_date(
            self.env, cover["principal_dob"], date_format="d MMM y"),
            "day first, month in words: 01/21 and 21/01 cannot be confused")
        self.assertEqual(members["Children"], "3")
        self.assertEqual(members["Requested limit"], "200,000 — 500,000")

    def test_matrix(self):
        matrix = self.comparison._odin_export_matrix()
        self.assertEqual([c["insurer"] for c in matrix["columns"]],
                         ["Test Afya Insurer", "Test Jamii Insurer"])
        self.assertEqual([c["highlight"] for c in matrix["columns"]], [False, True])
        rows = {row["label"]: row["cells"] for row in matrix["rows"]}
        self.assertEqual(rows["Inpatient limit"], [200000, 500000])
        self.assertEqual(rows["Outpatient limit"], [50000, None])
        self.assertEqual(rows["Dental limit"], [5000, None])
        self.assertEqual(rows["Inpatient"], [15822, 21286 + 17289 + 3 * 9033])
        self.assertEqual(rows["Outpatient"], [22130, None])
        self.assertAlmostEqual(rows["Discount"][0], -0.1 * (15822 + 22130 + 6050))
        self.assertEqual(rows["Policy charge"], [None, 500])
        orders = self.comparison.plan_ids.sorted("sequence").order_id
        for cell, order in zip(rows["Taxes and levies"], orders, strict=True):
            self.assertAlmostEqual(cell, order.amount_tax + 40,
                                   msg="levies and stamp duty together, as the client pays them")
        self.assertEqual(rows["Total premium"], orders.mapped("amount_total"))

    def test_columns_insurer_cover_then_product(self):
        """Insurer on top, then the hospital cover, then the insurer's own
        name for the product and plan, without the amount the cover says or
        the age variant ("Below 70") the engine rates on. Outpatient and
        dental limits say whom they cover."""
        matrix = self.comparison._odin_export_matrix()
        self.assertEqual([(c["insurer"], c["cover"], c["product"]) for c in matrix["columns"]],
                         [("Test Afya Insurer", 200000, "Afya · Plan 1"),
                          ("Test Jamii Insurer", 500000, "Jamii · Family Cover")])
        self.assertEqual([c["name"] for c in matrix["columns"]],
                         ["Afya · Plan 1 (KES 200,000)", "Jamii · Family Cover (KES 500,000)"])
        basis = {row["label"]: row.get("basis") for row in matrix["rows"]}
        self.assertEqual(basis["Outpatient limit"], ["per family", ""],
                         "Afya's outpatient is shared by the family")
        self.assertEqual(basis["Dental limit"], ["per person", ""])
        self.assertEqual(basis["Inpatient limit"], ["", ""], "the plan's own limit")
        text = self.html()
        self.assertIn("Afya · Plan 1", text)
        self.assertNotIn("Below 70", text)
        self.assertIn("per family", text)
        order = self.comparison.plan_ids.order_id.filtered(
            lambda o: o.sale_order_template_id == self.afya)
        self.assertEqual(order._odin_plan_label(), "Afya – Plan 1 (KES 200,000)",
                         "the staff's name for it: the whole name, no age variant")
        self.assertEqual(order._odin_plan_text(),
                         "Test Afya Insurer – %s 200,000 hospital cover (Afya · Plan 1)"
                         % order.currency_id.name)
        self.assertIn(order._odin_plan_text(), self.comparison._whatsapp_message(),
                      "the client's message names it the same way")

    def test_display_parts(self):
        """The amount leaves the plan's name only where the cover says it."""
        Template = self.env["sale.order.template"]
        cases = [
            ("GA Community Health – KES 1,000,000", 1000000, "Community Health"),
            ("GA Community Health – KES 1,000,000, with pandemic cover", 1000000,
             "Community Health, with pandemic cover"),
            ("GA Hadhi Health – Plan A (KES 5,000,000), with Covid-19 cover (groups of 10 or more)",
             5000000, "Hadhi Health · Plan A, with Covid-19 cover (groups of 10 or more)"),
            ("Jubilee J Care – Premier (KES 1,000,000) – Below 70", 1000000, "J Care · Premier"),
            ("Jubilee J Care – Premier (KES 1,000,000)", 500000, "J Care · Premier (KES 1,000,000)"),
            ("Jubilee J Care Junior – Classic (KES 500,000 per child)", 500000,
             "J Care Junior · Classic (per child)"),
        ]
        for name, cover, product in cases:
            insurer = self.env["res.partner"].create({"name": name.split()[0] + " Insurance",
                                                      "is_company": True})
            template = Template.new({"name": name, "odin_insurer_id": insurer.id})
            self.assertEqual(template._odin_display_parts(cover), (insurer.name, product), name)

    def test_whats_covered(self):
        """Each plan's own benefit notes, at the level quoted; a plan without
        any is left out, and payment terms are not cover."""
        matrix = self.comparison._odin_export_matrix()
        covered = self.comparison._odin_export_covered(matrix["columns"])
        self.assertEqual([plan["column"]["insurer"] for plan in covered], ["Test Afya Insurer"])
        self.assertEqual(covered[0]["groups"], [{
            "title": "Inpatient benefits",
            "items": [{"bullet": True, "text": "Overall limit: KES 200,000"}],
        }])
        text = self.html()
        self.assertIn("What's covered", text)
        self.assertIn("Overall limit: KES 200,000", text)
        self.assertNotIn("25% deposit", text, "payment terms are not cover")

    def test_report_renders(self):
        self.company.odin_quote_payment_note = "<p>PAYBILL: 880100</p>"
        text = self.html()
        for expected in ("David Muthuri Muriuki", "People included in this quotation",
                         "Principal DOB", "Cover and premium comparison", "Test Afya Insurer",
                         "Recommended", "Total premium", "Payment options", "PAYBILL: 880100",
                         "Quotation by Quote Agent", "For and on behalf of", self.comparison.name):
            self.assertIn(expected, text)
        self.assertIn("500,000", text)
        self.assertNotIn("500,000.00", text, "limits are printed as round sums")

    def test_terms_once(self):
        terms = "<p>Waiting periods: 30 days.</p>"
        self.comparison.plan_ids.order_id.write({"note": terms})
        exported = self.comparison._odin_export_terms()
        self.assertEqual(len(exported), 1, "the same terms are printed once, not once per plan")
        self.assertEqual(exported[0]["title"], "")
        self.assertEqual(self.html().count("Waiting periods"), 1)

    def test_payment_falls_back_to_bank_accounts(self):
        self.company.odin_quote_payment_note = False
        bank = self.env["res.bank"].create({"name": "NCBA Bank Kenya", "bic": "CBAFKENX"})
        self.company.partner_id.bank_ids = [Command.create({
            "acc_number": "4351890038", "bank_id": bank.id})]
        payment = str(self.comparison._odin_export_payment())
        self.assertIn("4351890038", payment)
        self.assertIn("CBAFKENX", payment)

    def test_print_and_whatsapp_use_it(self):
        action = self.comparison.action_print()
        self.assertEqual(action["report_name"], "odin_medical_quote.report_medical_quotation")
        attachment = self.comparison._comparison_pdf_attachment()
        self.assertEqual(attachment.name, "Medical quotation - David Muthuri Muriuki - %s.pdf"
                         % self.comparison.name)
        # A comparison built the old way still prints the old way.
        old = self.comparison.create({"partner_id": self.comparison.partner_id.id})
        self.assertEqual(old.action_print()["report_name"], "odin_client_quote.report_quote_comparison")

    def test_send_by_email(self):
        action = self.comparison.action_odin_send_email()
        composer = self.env["mail.compose.message"].with_user(self.agent).with_context(
            **action["context"]).create({})
        self.assertIn(self.comparison.name, composer.subject)
        afya = self.comparison.plan_ids.order_id.filtered(
            lambda o: o.sale_order_template_id == self.afya)
        self.assertIn("Test Afya Insurer – %s 200,000 hospital cover (Afya · Plan 1)"
                      % afya.currency_id.name, composer.body,
                      "the insurer and the cover first, as on the quotation")
        self.assertNotIn("Below 70", composer.body)
        self.assertIn(format_date(self.env, self.comparison.validity_date, date_format="d MMM y"),
                      composer.body, "the date as the PDF prints it")
        composer._action_send_mail()
        message = self.comparison.message_ids.filtered(lambda m: m.attachment_ids)[:1]
        # Tests render reports as HTML, so only the name is checked, not ".pdf".
        self.assertEqual([name.rsplit(".", 1)[0] for name in message.attachment_ids.mapped("name")],
                         ["Medical quotation - David Muthuri Muriuki - %s" % self.comparison.name])
        self.assertIn(self.comparison.partner_id, message.partner_ids)
        self.assertEqual(self.comparison.state, "sent")
