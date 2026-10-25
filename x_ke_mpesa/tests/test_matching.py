# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)
"""The matching engine and the allocation queue."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import MpesaCommon


@tagged("post_install", "-at_install")
class TestMatching(MpesaCommon):

    def pay_invoice_by_stk(self, invoice, amount):
        self.fake.script_stk(0)
        wizard = self.env["mpesa.stk.request"].with_context(
            default_move_id=invoice.id).create({"shortcode_id": self.paybill.id, "amount": amount})
        wizard.action_send()
        self.deliver()
        return wizard.transaction_id

    def test_stk_pays_its_invoice(self):
        invoice = self.invoice(1000)
        tx = self.pay_invoice_by_stk(invoice, 1000)
        self.assertEqual(tx.allocation_state, "allocated")
        self.assertTrue(tx.statement_line_id.is_reconciled)
        self.assertEqual(invoice.payment_state, "paid")

    def test_whole_shilling_rounding_is_written_off(self):
        rounding = self.env["account.account"].create({
            "name": "Rounding", "code": "999901", "account_type": "expense"})
        self.company.mpesa_rounding_account_id = rounding
        invoice = self.invoice(1234.40)
        tx = self.pay_invoice_by_stk(invoice, 1234)
        self.assertEqual(tx.allocation_state, "allocated")
        self.assertEqual(invoice.payment_state, "paid")
        lines = tx.statement_line_id.move_id.line_ids.filtered(lambda l: l.account_id == rounding)
        self.assertAlmostEqual(sum(lines.mapped("balance")), 0.40, places=2)

    def test_part_payment(self):
        invoice = self.invoice(1000)
        tx = self.pay_invoice_by_stk(invoice, 400)
        self.assertEqual(tx.allocation_state, "allocated")
        self.assertEqual(invoice.payment_state, "partial")
        self.assertAlmostEqual(invoice.amount_residual, 600)

    def test_paybill_account_number_is_the_invoice(self):
        invoice = self.invoice(750)
        receipt = self.fake.c2b_payment("600200", 750, bill_ref=invoice.name.replace("/", " "))
        self.deliver()
        tx = self.transactions(receipt=receipt)
        self.assertEqual(tx.allocation_state, "allocated")
        self.assertEqual(invoice.payment_state, "paid")
        self.assertEqual(tx.partner_id, self.partner_a)

    def test_paybill_account_number_is_the_customer(self):
        self.partner_b.mpesa_account_ref = "UNIT-B12"
        older = self.invoice(300, partner=self.partner_b)
        newer = self.invoice(500, partner=self.partner_b)
        receipt = self.fake.c2b_payment("600200", 1000, bill_ref="unit b12")
        self.deliver()
        tx = self.transactions(receipt=receipt)
        self.assertEqual(tx.allocation_state, "allocated")
        self.assertEqual(older.payment_state, "paid")
        self.assertEqual(newer.payment_state, "paid")
        # 200 left over: a credit on the customer, not lost in suspense.
        receivable = self.partner_b.property_account_receivable_id
        credit = tx.statement_line_id.move_id.line_ids.filtered(
            lambda l: l.account_id == receivable and not l.reconciled)
        self.assertAlmostEqual(sum(credit.mapped("balance")), -200)

    def test_customer_without_open_invoice_is_on_account(self):
        self.partner_b.mpesa_account_ref = "UNIT-C1"
        receipt = self.fake.c2b_payment("600200", 5000, bill_ref="UNIT-C1")
        self.deliver()
        tx = self.transactions(receipt=receipt)
        self.assertEqual(tx.allocation_state, "allocated", "a deposit sits on the customer")

    def test_unknown_reference_stays_unallocated(self):
        receipt = self.fake.c2b_payment("600200", 100, bill_ref="ZZZ")
        self.deliver()
        self.assertEqual(self.transactions(receipt=receipt).allocation_state, "unallocated")

    def test_rule_can_be_switched_off(self):
        self.company.mpesa_match_paybill = False
        invoice = self.invoice(750)
        receipt = self.fake.c2b_payment("600200", 750, bill_ref=invoice.name)
        self.deliver()
        self.assertEqual(self.transactions(receipt=receipt).allocation_state, "unallocated")
        self.assertEqual(invoice.payment_state, "not_paid")

    def test_allocate_wizard(self):
        invoice = self.invoice(900)
        receipt = self.fake.c2b_payment("600100", 900)
        self.deliver()
        tx = self.transactions(receipt=receipt)
        wizard = self.env["mpesa.allocate"].create({
            "transaction_ids": [(6, 0, tx.ids)], "mode": "invoice",
            "partner_id": self.partner_a.id, "invoice_ids": [(6, 0, invoice.ids)]})
        wizard.action_confirm()
        self.assertEqual(tx.allocation_state, "allocated")
        self.assertEqual(invoice.payment_state, "paid")

    def test_allocate_on_account(self):
        receipt = self.fake.c2b_payment("600100", 900)
        self.deliver()
        tx = self.transactions(receipt=receipt)
        self.env["mpesa.allocate"].create({
            "transaction_ids": [(6, 0, tx.ids)], "mode": "partner",
            "partner_id": self.partner_b.id}).action_confirm()
        self.assertEqual(tx.allocation_state, "allocated")
        self.assertEqual(tx.partner_id, self.partner_b)

    def test_park_and_unpark(self):
        receipt = self.fake.c2b_payment("600100", 900)
        self.deliver()
        tx = self.transactions(receipt=receipt)
        self.env["mpesa.allocate"].create({
            "transaction_ids": [(6, 0, tx.ids)], "mode": "park",
            "reason": "No account number"}).action_confirm()
        self.assertEqual(tx.allocation_state, "parked")
        self.assertFalse(tx.statement_line_id.is_reconciled)
        self.assertEqual(tx.statement_line_id.journal_id.suspense_account_id,
                         self.company.mpesa_suspense_account_id)
        tx.action_unpark()
        self.assertEqual(tx.allocation_state, "unallocated")
        with self.assertRaises(UserError):
            tx.action_park("")
