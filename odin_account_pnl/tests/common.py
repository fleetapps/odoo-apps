"""Fixtures for the P&L tests: one company, an account of every P&L type, a
balance-sheet counterpart, two partners, an analytic plan with two
departments, and a helper posting a journal entry in one line."""

from odoo import Command
from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class PnlCase(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report = cls.env["odin.pnl.report"]
        Account = cls.env["account.account"]
        cls.revenue = cls.company_data["default_account_revenue"]
        cls.opex = cls.company_data["default_account_expense"]
        cls.cos = Account.create({"name": "Cost of goods", "code": "510099", "account_type": "expense_direct_cost"})
        cls.depreciation = Account.create({"name": "Depreciation", "code": "680099", "account_type": "expense_depreciation"})
        cls.other_income = Account.create({"name": "Interest received", "code": "790099", "account_type": "income_other"})
        cls.other_expense = Account.create({"name": "FX loss", "code": "690099", "account_type": "expense_other"})
        cls.counterpart = Account.create({"name": "Clearing", "code": "199099", "account_type": "asset_current"})
        cls.misc = cls.company_data["default_journal_misc"]
        cls.misc2 = cls.misc.copy({"name": "Second misc", "code": "MSC2"})
        cls.plan = cls.env["account.analytic.plan"].create({"name": "Departments"})
        cls.sales_dept = cls.env["account.analytic.account"].create({"name": "Sales", "plan_id": cls.plan.id})
        cls.product_dept = cls.env["account.analytic.account"].create({"name": "Product", "plan_id": cls.plan.id})
        cls.accountant = new_test_user(
            cls.env, login="pnl_accountant",
            groups="base.group_user,account.group_account_user",
            company_id=cls.env.company.id, company_ids=[cls.env.company.id])
        cls.billing = new_test_user(
            cls.env, login="pnl_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=cls.env.company.id, company_ids=[cls.env.company.id])

    @classmethod
    def post(cls, date, lines, journal=None, state="posted"):
        """Post an entry on ``date``. ``lines``: tuples ``(account, amount)``
        with amount > 0 a debit, plus optional ``partner=`` and ``analytic=``
        keys in a third dict element. The clearing account balances it."""
        commands = []
        total = 0.0
        for line in lines:
            account, amount = line[0], line[1]
            extra = line[2] if len(line) > 2 else {}
            total += amount
            commands.append(Command.create({
                "account_id": account.id,
                "debit": max(amount, 0.0),
                "credit": max(-amount, 0.0),
                "partner_id": extra.get("partner") and extra["partner"].id,
                "product_id": extra.get("product") and extra["product"].id,
                "analytic_distribution": extra.get("analytic"),
                "name": extra.get("label", "test"),
            }))
        commands.append(Command.create({
            "account_id": cls.counterpart.id,
            "debit": max(-total, 0.0),
            "credit": max(total, 0.0),
            "name": "clearing",
        }))
        move = cls.env["account.move"].create({
            "move_type": "entry",
            "date": date,
            "journal_id": (journal or cls.misc).id,
            "line_ids": commands,
        })
        if state == "posted":
            move.action_post()
        return move

    def options(self, **values):
        options = {"date": {"filter": "custom", "date_from": "2026-09-01", "date_to": "2026-09-30"}, "trend": False}
        options.update(values)
        return options

    def run_report(self, user=None, **values):
        report = self.report.with_user(user) if user else self.report
        return report.get_report(self.options(**values))

    @staticmethod
    def row(result, key):
        return next((row for row in result["rows"] if row["key"] == key), None)

    def value(self, result, key, column="c0"):
        row = self.row(result, key)
        return row and row["values"].get(column)
