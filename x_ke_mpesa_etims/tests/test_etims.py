# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMpesaEtims(TransactionCase):

    def guess(self, **vals):
        return self.env["pos.payment.method"].new(vals)._ke_etims_guess_payment_type()

    def test_an_mpesa_terminal_is_mobile_money(self):
        self.assertEqual(self.guess(name="Westlands counter", use_payment_terminal="mpesa"), "06",
                         "not a card, though it is a payment terminal")
        self.assertEqual(self.guess(name="M-Pesa 600100", use_payment_terminal="mpesa"), "06")

    def test_other_methods_as_before(self):
        bank = self.env["account.journal"].search(
            [("type", "=", "bank"), ("company_id", "=", self.env.company.id)], limit=1)
        self.assertEqual(self.guess(name="Bank card", journal_id=bank.id), "05")
        self.assertEqual(self.guess(name="Customer account"), "02", "no journal: pay later")
