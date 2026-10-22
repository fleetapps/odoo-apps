from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import BarDeskCase


@tagged("post_install", "-at_install")
class TestProfitAndLoss(BarDeskCase):
    """The P&L a bar sees, set up as Dhostana's books are: periodic
    valuation at average cost, supplier bills to Cost of sales, and Odoo's
    stock closing booking the change in stock value to Stock Variation --
    which has to sit in Cost of Revenue too, or the gross profit is only
    revenue less purchases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.company
        Account = cls.env["account.account"]
        cls.cost_of_sales = Account.create(
            {"name": "Cost of sales 1", "code": "590100", "account_type": "expense_direct_cost"}
        )
        cls.stock_variation = Account.create(
            {"name": "Stock Variation", "code": "590105", "account_type": "expense_direct_cost"}
        )
        # As tools/dhostana_pnl.py leaves it: a variation account, and no
        # stock expense account to set off Odoo's "continental" step.
        cls.stock_account = Account.create(
            {
                "name": "Stock",
                "code": "190100",
                "account_type": "asset_current",
                "account_stock_variation_id": cls.stock_variation.id,
            }
        )
        cls.opening_equity = Account.create({"name": "Opening balance", "code": "390100", "account_type": "equity"})
        company.write(
            {
                "account_stock_valuation_id": cls.stock_account.id,
                "expense_account_id": cls.cost_of_sales.id,
                "inventory_period": "daily",
            }
        )
        beer = cls.env["product.category"].create({"name": "Beer, RTD & Cider"}).with_company(company)
        beer.write(
            {
                "property_cost_method": "average",
                "property_valuation": "periodic",
                "property_account_expense_categ_id": cls.cost_of_sales.id,
                "property_stock_valuation_account_id": cls.stock_account.id,
            }
        )
        cls.tusker.write({"categ_id": beer.id, "supplier_taxes_id": [Command.clear()]})
        cls.tusker.with_company(company).with_context(disable_auto_revaluation=True).standard_price = 100.0
        cls.supplier = cls.env["res.partner"].create({"name": "Tony West", "is_company": True, "supplier_rank": 1})
        cls.store.supplier_payment_journal_id = cls.env["account.journal"].search(
            [("type", "in", ("cash", "bank")), ("company_id", "=", company.id)], limit=1
        )
        # The stock on the shelves when the club started on Odoo, and the
        # accountant's opening entry for it.
        cls.opening_stock(cls.loc_store, [(cls.tusker, 100)])
        cls.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": company.account_stock_journal_id.id,
                "ref": "Opening stock",
                "line_ids": [
                    Command.create({"account_id": cls.stock_account.id, "debit": 10000.0}),
                    Command.create({"account_id": cls.opening_equity.id, "credit": 10000.0}),
                ],
            }
        ).action_post()

    def balance(self, accounts):
        lines = self.env["account.move.line"].search(
            [("account_id", "in", accounts.ids), ("parent_state", "=", "posted")]
        )
        return sum(lines.mapped("balance"))

    def cost_of_revenue(self):
        return self.balance(self.env["account.account"].search([("account_type", "=", "expense_direct_cost")]))

    def test_cost_of_revenue_is_what_was_used_up(self):
        tusker = self.tusker.with_company(self.company)
        token = self.login(self.device_store, self.store, self.sam, "4321")
        # Two crates of 24 at 3,000 a crate: 125 a bottle.
        self.desk(self.device_store).desk_supplier_delivery(
            self.store.id, token, self.uuid(), self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "invoiced": 2, "received": 2, "price": 3000}],
            paid="now",
        )
        self.assertAlmostEqual(self.balance(self.cost_of_sales), 6000.0, msg="the bill is a purchase")
        self.assertAlmostEqual(tusker.standard_price, 16000 / 148, places=4, msg="100 at 100 and 48 at 125")
        sold = self.post(self.type_sal_be, self.loc_store, self.loc_customers, [(self.tusker, 60, None)])
        # Two bottles short at the count.
        quant = self.env["stock.quant"].with_context(inventory_mode=True).search(
            [("product_id", "=", self.tusker.id), ("location_id", "=", self.loc_store.id)]
        )
        quant.inventory_quantity = 86
        quant.action_apply_inventory()
        short = self.env["stock.move"].search(
            [("product_id", "=", self.tusker.id), ("is_out", "=", True), ("picking_id", "=", False)], order="id desc", limit=1
        )

        self.company.action_close_stock_valuation(auto_post=True)

        # Each move's value is rounded to the cent, hence the cent of slack.
        used_up = sold.move_ids.value + short.value
        self.assertAlmostEqual(used_up, 62 * 16000 / 148, delta=0.02)
        self.assertAlmostEqual(
            self.cost_of_revenue(), used_up, delta=0.02,
            msg="purchases less the stock still on the shelf: what was sold and what went missing",
        )
        self.assertAlmostEqual(self.balance(self.stock_account), tusker.total_value, places=2)
        # Read line by line: purchases, then what the stock fell by.
        self.assertAlmostEqual(self.balance(self.cost_of_sales), 6000.0, places=2)
        self.assertAlmostEqual(self.balance(self.stock_variation), 10000.0 - tusker.total_value, places=2)
        self.assertAlmostEqual(tusker.total_value, 86 * 16000 / 148, places=2)
        with self.assertRaisesRegex(UserError, "Everything is correctly closed"):
            self.company.action_close_stock_valuation(auto_post=True)

    def test_buying_stock_is_not_a_cost_until_it_is_used(self):
        """A big delivery the day before the month closes does not sink the
        month: what is still on the shelf goes back to the balance sheet."""
        token = self.login(self.device_store, self.store, self.sam, "4321")
        self.desk(self.device_store).desk_supplier_delivery(
            self.store.id, token, self.uuid(), self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "invoiced": 20, "received": 20, "price": 2400}],
            paid="later",
        )
        self.assertAlmostEqual(self.cost_of_revenue(), 48000.0)
        self.company.action_close_stock_valuation(auto_post=True)
        self.assertAlmostEqual(self.cost_of_revenue(), 0.0, places=2, msg="nothing was sold")
        self.assertAlmostEqual(self.balance(self.stock_account), 58000.0, places=2)

    def test_the_closing_runs_every_day_by_itself(self):
        token = self.login(self.device_store, self.store, self.sam, "4321")
        self.desk(self.device_store).desk_supplier_delivery(
            self.store.id, token, self.uuid(), self.supplier.id,
            [{"product_id": self.tusker.id, "uom_id": self.uom_crate.id, "invoiced": 1, "received": 1, "price": 2400}],
        )
        self.env["res.company"]._cron_post_stock_valuation()
        self.assertAlmostEqual(self.balance(self.stock_account), 12400.0, places=2)
