# -*- coding: utf-8 -*-
"""Who can look, who can prepare, and who can close.

Closing a period writes the company's tax lock date, which stops every user of
every app from posting into it. That is not a preparer's decision, so it is
gated in Python and not only on the button - a ``groups=`` attribute hides a
button and stops nothing that arrives over RPC.
"""

from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import KeVatCase


@tagged("post_install_l10n", "post_install", "-at_install")
class TestSecurity(KeVatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.preparer = new_test_user(
            cls.env, login="ke_vat_preparer",
            groups="x_ke_base.group_ke_tax_user",
            company_id=cls.ke_company.id)
        # Deliberately only our group. If group_ke_tax_manager does not
        # imply enough accounting access on its own, these tests fail - which
        # is the point, because on a real database that is all a VAT manager
        # will hold.
        cls.manager = new_test_user(
            cls.env, login="ke_vat_manager",
            groups="x_ke_base.group_ke_tax_manager",
            company_id=cls.ke_company.id)

    def test_a_preparer_can_compute_a_return(self):
        self.post_invoice("ST16", 100_000.0)
        vat_return = self.make_return().with_user(self.preparer)
        vat_return.action_compute()

        box_1 = vat_return.line_ids.filtered(lambda l: l.code == "box_1")
        self.assertEqual(box_1.base_value, 100_000.0)

    def test_a_preparer_cannot_close_a_period(self):
        vat_return = self.make_return().with_user(self.preparer)
        with self.assertRaises(AccessError):
            vat_return.action_close()

    def test_a_preparer_cannot_reopen_a_period(self):
        vat_return = self.make_return()
        vat_return.action_close()
        with self.assertRaises(AccessError):
            vat_return.with_user(self.preparer).action_reopen()

    def test_a_manager_can_close_a_period(self):
        vat_return = self.make_return().with_user(self.manager)
        vat_return.action_close()
        self.assertEqual(vat_return.state, "closed")

    def test_a_preparer_cannot_run_the_export_repair(self):
        with self.assertRaises(AccessError):
            self.env["ke.tax.retag.wizard"].with_user(
                self.preparer).create({"company_id": self.ke_company.id})

    def test_a_preparer_can_open_the_journal_items_behind_a_box(self):
        # account.move.line read is granted only to the accounting groups, so
        # without group_ke_tax_user implying account.group_account_readonly
        # every drill-down raises AccessError.
        self.post_invoice("ST16", 100_000.0)
        vat_return = self.make_return().with_user(self.preparer)
        vat_return.action_compute()

        box_1 = vat_return.line_ids.filtered(lambda l: l.code == "box_1")
        action = box_1.action_drill()
        lines = self.env["account.move.line"].with_user(
            self.preparer).search(action["domain"])
        self.assertTrue(lines)

    def test_a_manager_can_post_the_vat_entry_on_close(self):
        company = self.ke_company
        company.ke_vat_post_on_close = True
        company.ke_vat_journal_id = self.company_data["default_journal_misc"]
        company.ke_vat_output_account_id = self.company_data[
            "default_account_tax_sale"]
        company.ke_vat_input_account_id = self.company_data[
            "default_account_tax_purchase"]
        company.ke_vat_payable_account_id = self.company_data[
            "default_account_payable"]
        company.ke_vat_credit_account_id = self.company_data[
            "default_account_receivable"]
        self.post_invoice("ST16", 1_000_000.0)

        vat_return = self.make_return().with_user(self.manager)
        vat_return.action_close()

        self.assertEqual(
            vat_return.move_id.state, "posted",
            "Closing posts a journal entry, so the manager group has to carry "
            "write access to account.move on its own.")

    def test_an_accountant_holds_the_kenya_tax_user_role(self):
        # The bake's "Accountant" role is account.group_account_user and
        # nothing else; an App Store customer's accountants are the same.
        accountant = new_test_user(
            self.env, login="ke_accountant",
            groups="account.group_account_user",
            company_id=self.ke_company.id)
        self.assertTrue(accountant.has_group("x_ke_base.group_ke_tax_user"))
        self.assertFalse(
            accountant.has_group("x_ke_base.group_ke_tax_manager"),
            "Closing a period sets a company-wide lock; it stays an explicit grant.")

    def test_another_companys_return_is_invisible(self):
        other = self.setup_other_company()["company"]
        other_return = self.env["ke.vat.return"].create({
            "company_id": other.id,
            "report_id": self.vat3.id,
            "date_from": self.period_from,
            "date_to": self.period_to,
        })

        visible = self.env["ke.vat.return"].with_user(self.preparer).search(
            [("id", "=", other_return.id)])
        self.assertFalse(
            visible,
            "A VAT return belongs to one company and nobody, of any role, "
            "should see another company's tax position.")
