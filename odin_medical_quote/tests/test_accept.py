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
        self.assertIn(self.jamii.name, wizard.plan_summary)
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

    def test_deposit(self):
        wizard = self.wizard(order_id=self.afya_order.id, invoice_mode="deposit", deposit_percent=25)
        self.assertAlmostEqual(wizard.deposit_amount, self.afya_order.currency_id.round(
            self.afya_order.amount_total / 4))
        action = wizard.action_accept()
        invoice = self.env["account.move"].browse(action["res_id"])
        self.assertAlmostEqual(invoice.amount_total, wizard.deposit_amount, places=2)
        self.assertEqual(self.jamii_order.state, "cancel")

    def test_confirm_only(self):
        action = self.wizard(invoice_mode="later").action_accept()
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
