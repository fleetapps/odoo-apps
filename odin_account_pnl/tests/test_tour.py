"""The page in a real browser: unfold, split, peek, open, come back."""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import HttpCase, tagged

from .common import PnlCase


@tagged("post_install", "-at_install")
class TestTour(PnlCase, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        customer = cls.env["res.partner"].create({"name": "Tour Customer"})
        day = fields.Date.today().replace(day=1) - relativedelta(months=1) + relativedelta(days=14)
        cls.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": customer.id,
            # The browser is not frozen in time and the report opens on last month.
            "invoice_date": day,
            "date": day,
            "invoice_line_ids": [(0, 0, {
                "name": "Consulting", "quantity": 1, "price_unit": 1000.0,
                "account_id": cls.revenue.id, "tax_ids": [(6, 0, [])],
            })],
        }).action_post()

    def _sign_in_admin(self):
        # The accounting test base works in a company of its own: sign the
        # tour's user into it.
        self.env.ref("base.user_admin").write({
            "company_ids": [(4, self.env.company.id)],
            "company_id": self.env.company.id,
            "group_ids": [(4, self.env.ref("account.group_account_user").id)],
        })

    def test_unfold_peek_open_and_come_back(self):
        self._sign_in_admin()
        self.start_tour(
            "/odoo/action-odin_account_pnl.odin_pnl_report_action?debug=",
            "odin_account_pnl_tour",
            login="admin",
        )

    def test_ledger_balance_and_shortcuts(self):
        self._sign_in_admin()
        self.start_tour(
            "/odoo/action-odin_account_pnl.odin_pnl_report_action?debug=",
            "odin_account_pnl_ledger_tour",
            login="admin",
        )
