"""Who sees the P&L, and that it only counts what the user may read."""

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestAccess(PnlCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-09-10", [(cls.revenue, -1000.0)])
        cls.other = cls.setup_other_company()
        other_company = cls.other["company"]
        other_revenue = cls.other["default_account_revenue"]
        other_clearing = cls.env["account.account"].with_company(other_company).create({
            "name": "Clearing", "code": "199099", "account_type": "asset_current",
            "company_ids": [other_company.id]})
        move = cls.env["account.move"].with_company(other_company).create({
            "move_type": "entry", "date": "2026-09-10", "journal_id": cls.other["default_journal_misc"].id,
            "line_ids": [
                (0, 0, {"account_id": other_revenue.id, "credit": 5000.0}),
                (0, 0, {"account_id": other_clearing.id, "debit": 5000.0}),
            ],
        })
        move.action_post()

    def test_a_billing_user_cannot_open_the_report(self):
        with self.assertRaises(AccessError):
            self.run_report(user=self.billing)

    def test_an_accountant_sees_only_their_company(self):
        result = self.run_report(user=self.accountant)
        self.assertAlmostEqual(self.value(result, "L:REV"), 1000.0)

    def test_two_companies_selected_add_up(self):
        report = self.report.with_context(allowed_company_ids=[self.env.company.id, self.other["company"].id])
        result = report.get_report(self.options())
        self.assertAlmostEqual(self.value(result, "L:REV"), 6000.0)
