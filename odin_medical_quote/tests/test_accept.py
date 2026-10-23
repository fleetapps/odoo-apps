from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MedicalQuoteCase


@tagged("post_install", "-at_install")
class TestAccept(MedicalQuoteCase):
    """Client accepted: one plan confirmed and invoiced, the rest cancelled,
    the opportunity won -- by a salesperson with no accounting rights."""

    def setUp(self):
        super().setUp()
        result = self.search()
        afya = self.plan(result, self.afya)
        saved = self.Quote.save_quote({
            "customer": {"source": "new", "name": "Henry", "phone": "0798726720"},
            "cover": self.cover(),
            "plans": [
                {"template_id": self.afya.id, "options": [self.option(afya, "outpatient")]},
                {"template_id": self.jamii.id, "options": [], "recommended": True},
            ],
        })
        self.comparison = self.env["odin.quote.comparison"].with_user(self.agent).browse(
            saved["comparison"]["id"])
        self.lead = self.comparison.opportunity_id
        self.orders = self.comparison.plan_ids.order_id
        self.jamii_order = self.orders.filtered(lambda o: o.sale_order_template_id == self.jamii)
        self.afya_order = self.orders - self.jamii_order

    def wizard(self, **values):
        action = self.comparison.action_odin_accept()
        Wizard = self.env["odin.medical.quote.accept"].with_user(self.agent).with_context(
            **action["context"])
        return Wizard.create(values)

    def test_full_invoice(self):
        self.assertFalse(self.agent.has_group("account.group_account_invoice"))
        wizard = self.wizard()
        self.assertEqual(wizard.order_id, self.jamii_order, "the recommended plan first")
        self.assertEqual(wizard.other_order_ids, self.afya_order)
        self.assertIn(self.jamii.name, wizard.order_id.display_name,
                      "the plan's name where it is picked, not just S00042")
        action = wizard.action_accept()

        self.assertEqual(self.jamii_order.state, "sale")
        self.assertEqual(self.afya_order.state, "cancel")
        self.assertEqual(self.lead.won_status, "won")
        self.assertEqual(self.comparison.state, "accepted")
        self.assertEqual(self.comparison.odin_accepted_order_id, self.jamii_order)
        invoice = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(invoice.state, "draft")
        self.assertAlmostEqual(invoice.amount_total, self.jamii_order.amount_total)
        self.assertEqual(self.comparison.odin_invoice_count, 1)

        with self.assertRaisesRegex(UserError, "accepted"):
            self.Quote.save_quote(dict(self.comparison.odin_builder_params,
                                       comparison_id=self.comparison.id))
        with self.assertRaises(UserError):
            self.comparison.action_odin_edit()

    def test_instalments(self):
        """Paid like a loan: equal monthly payments, the first now, the
        financing on the whole premium -- on the order, and the invoice due
        in the same parts."""
        plan = self.env["odin.quote.instalment.plan"].create({
            "name": "10 monthly instalments", "months": 10, "monthly_fee": 1000})
        self.comparison.sudo().instalment_plan_ids = plan
        premium = self.jamii_order.amount_total
        wizard = self.wizard(payment="instalments", instalment_plan_id=plan.id)
        self.assertEqual(wizard.instalment_months, 10)
        self.assertAlmostEqual(wizard.instalment_amount, round((premium + 10000) / 10, 2))
        self.assertAlmostEqual(wizard.instalment_financing, wizard.instalment_payable - premium)
        invoice = self.env["account.move"].browse(wizard.action_accept()["res_id"])

        financing = self.jamii_order.order_line.filtered(
            lambda l: l.product_id == self.env.ref("odin_medical_quote.product_financing"))
        self.assertAlmostEqual(financing.price_total, wizard.instalment_financing)
        self.assertFalse(financing.tax_ids, "no levies on financing")
        self.assertAlmostEqual(invoice.amount_total, wizard.instalment_payable)
        dues = invoice.line_ids.filtered(lambda l: l.display_type == "payment_term").sorted("date_maturity")
        self.assertEqual(len(dues), 10)
        self.assertEqual(dues[0].date_maturity, invoice.invoice_date or dues[0].date_maturity)
        self.assertEqual(len(set(round(abs(d.amount_currency), 0) for d in dues)), 1, "equal parts")

        # Printed in the quotation's look, by default from now on.
        report = self.env.ref("odin_medical_quote.action_report_insurance_invoice")
        self.assertEqual(self.jamii_order.partner_id.commercial_partner_id.invoice_template_pdf_report_id, report)
        html, _type = self.env["ir.actions.report"]._render_qweb_html(report.report_name, invoice.ids)
        text = str(html)
        for expected in ("Billed to", "People included in this quotation", "Instalments",
                         "1 of 10", "Taxes and levies", self.comparison.name):
            self.assertIn(expected, text)

    def test_financing_is_replaced_not_added(self):
        plan = self.env["odin.quote.instalment.plan"].create({
            "name": "3 monthly instalments", "months": 3, "monthly_rate": 3})
        wizard = self.wizard(payment="instalments", instalment_plan_id=plan.id)
        wizard._odin_finance(self.jamii_order)
        wizard._odin_finance(self.jamii_order)
        product = self.env.ref("odin_medical_quote.product_financing")
        self.assertEqual(len(self.jamii_order.order_line.filtered(lambda l: l.product_id == product)), 1)

    def test_confirm_only(self):
        action = self.wizard(payment="later").action_accept()
        self.assertEqual(action["res_model"], "sale.order")
        self.assertEqual(self.jamii_order.state, "sale")
        self.assertFalse(self.jamii_order.invoice_ids)

    def test_blocked_confirmation(self):
        """An exception rule or a credit limit stops the order: its dialog is
        shown and nothing else happens until it goes through."""
        popup = {"type": "ir.actions.act_window", "res_model": "sale.exception.confirm"}
        with patch.object(type(self.env["sale.order"]), "action_confirm", lambda self: popup):
            result = self.wizard().action_accept()
        self.assertEqual(result, popup)
        self.assertEqual(self.afya_order.state, "draft", "nothing cancelled")
        self.assertNotEqual(self.lead.won_status, "won")
        self.assertEqual(self.comparison.state, "draft")

    def test_opened_from_crm(self):
        action = self.lead.action_odin_medical_quote()
        self.assertEqual(action["tag"], "odin_medical_quote")
        self.assertEqual(action["params"], {"lead_id": self.lead.id})
        self.assertEqual(self.lead.odin_comparison_count, 1)
        setup = self.Quote.get_setup(lead_id=self.lead.id)
        self.assertEqual(setup["lead"]["id"], self.lead.id)
        self.assertEqual(sorted(setup["insurers"]), ["Test Afya Insurer", "Test Jamii Insurer"])

    def test_confirmed_elsewhere(self):
        """Confirmed from the quotation itself, or by the client signing on
        the portal: the quote is accepted all the same."""
        self.jamii_order.action_confirm()
        self.assertEqual(self.comparison.state, "accepted")
        self.assertEqual(self.comparison.odin_accepted_order_id, self.jamii_order)
        self.assertEqual(self.afya_order.state, "cancel")
        self.assertEqual(self.lead.won_status, "won")
        # Client accepted still invoices it afterwards.
        action = self.comparison.action_odin_accept()
        wizard = self.env["odin.medical.quote.accept"].with_user(self.agent).with_context(
            **action["context"]).create({"order_id": self.jamii_order.id})
        invoice = self.env["account.move"].browse(wizard.action_accept()["res_id"])
        self.assertAlmostEqual(invoice.amount_total, self.jamii_order.amount_total)

    def test_declined(self):
        action = self.comparison.action_odin_decline()
        self.assertEqual(action["res_model"], "crm.lead.lost")
        wizard = self.env["crm.lead.lost"].with_user(self.agent).with_context(
            **action["context"]).create({})
        wizard.action_lost_reason_apply()
        self.assertFalse(self.lead.active)
        self.assertEqual(self.comparison.state, "declined")
        self.assertEqual(self.orders.mapped("state"), ["cancel", "cancel"])

        # Quoting them again brings the opportunity back.
        params = dict(self.comparison.odin_builder_params, comparison_id=self.comparison.id)
        self.Quote.save_quote(params)
        self.assertTrue(self.lead.active)
        self.assertEqual(self.comparison.state, "draft")
        self.assertEqual(set(self.comparison.plan_ids.order_id.mapped("state")), {"draft"})

    def test_declined_then_confirmed(self):
        self.lead.action_set_lost()
        self.assertEqual(self.comparison.state, "declined")
        self.jamii_order.action_draft()
        self.jamii_order.action_confirm()
        self.assertEqual(self.comparison.state, "accepted")
        self.assertTrue(self.lead.active)
        self.assertEqual(self.lead.won_status, "won", "the sale counts in the pipeline")

    def test_declined_without_opportunity(self):
        self.comparison.sudo().opportunity_id = False
        self.comparison.action_odin_decline()
        self.assertEqual(self.comparison.state, "declined")
        self.assertEqual(set(self.orders.mapped("state")), {"cancel"})
