"""The Ask tools on real books: names the model gives (journals, partners,
analytic accounts, budgets), budgets against actuals, receivables and
payables by age, and what the Ask page is told before the first question."""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import new_test_user, tagged

from .common import AiCase

PNL = {"date_from": "2026-09-01", "date_to": "2026-09-30", "comparison": "none", "line_code": None,
       "budget": "", "journal": "", "partner": "", "analytic_account": ""}


@tagged("post_install", "-at_install")
class TestAskTools(AiCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.post("2026-09-10", [(cls.revenue, -1000.0, {"partner": cls.partner_a,
                                                        "analytic": {str(cls.sales_dept.id): 100}})])
        cls.post("2026-09-12", [(cls.revenue, -400.0, {"partner": cls.partner_b,
                                                       "analytic": {str(cls.product_dept.id): 100}})],
                 journal=cls.misc2)
        # The owner's view: conversations are private to whoever asked.
        Conversation = cls.env["odin.ai.conversation"].with_user(cls.accountant)
        cls.conversation = Conversation.browse(Conversation.ask_start("Test?"))

    def run_tool(self, name, args):
        return self.env["odin.ai.tools"].with_user(self.accountant).run(self.conversation, name, args)

    def revenue_of(self, result):
        """Revenue for September in a pnl result (``self.revenue`` is the account)."""
        self.assertNotIn("error", result, result.get("error"))
        return next(row for row in result["rows"] if row["code"] == "REV")["values"]["Sep 2026"]

    # ------------------------------------------------------------------
    # The P&L tools
    # ------------------------------------------------------------------

    def test_a_line_code_that_gives_no_accounts_says_why(self):
        result, _label = self.run_tool("pnl", dict(PNL, line_code="NOPE"))
        self.assertIn("Unknown line code NOPE", result["error"])
        self.assertIn("REV", result["error"], "the valid codes are listed")
        result, _label = self.run_tool("pnl", dict(PNL, line_code="GP"))
        self.assertIn("computed from other lines", result["error"])
        result, _label = self.run_tool("pnl", dict(PNL, line_code="DEP"))
        self.assertEqual(result["rows"], [], "an accounts line with nothing posted is simply empty")

    def test_figures_narrowed_to_a_journal_a_partner_or_an_analytic_account(self):
        self.assertEqual(self.revenue_of(self.run_tool("pnl", PNL)[0]), 1400.0)
        # Odoo names a copied journal "<name> (copy)", whatever name is given.
        self.assertEqual(self.misc2.name, f"{self.misc.name} (copy)")
        self.assertEqual(self.revenue_of(self.run_tool("pnl", dict(PNL, journal=self.misc2.name))[0]), 400.0)
        self.assertEqual(self.revenue_of(self.run_tool("pnl", dict(PNL, journal=self.misc.name))[0]), 1000.0,
                         "the exact name wins over the copy that contains it")
        self.assertEqual(self.revenue_of(self.run_tool("pnl", dict(PNL, partner=self.partner_a.name))[0]), 1000.0)
        result, _label = self.run_tool("pnl", dict(PNL, analytic_account="Product"))
        self.assertEqual(self.revenue_of(result), 400.0)
        self.assertEqual(result["_scope"]["label"], "Sep 2026 (Product)")
        evidence = self.conversation._thread()["evidence"]
        self.assertTrue(evidence, "rows keep their evidence handles")

    def test_an_ambiguous_name_lists_the_candidates(self):
        self.env["account.analytic.account"].create({"name": "Product launch", "plan_id": self.plan.id})
        result, _label = self.run_tool("pnl", dict(PNL, analytic_account="Prod"))
        self.assertIn("Several analytic accounts match", result["error"])
        self.assertIn("Product launch", result["error"])
        result, _label = self.run_tool("pnl", dict(PNL, analytic_account="Product"))
        self.assertEqual(self.revenue_of(result), 400.0, "an exact name wins over partial matches")
        result, _label = self.run_tool("pnl", dict(PNL, journal="Nowhere"))
        self.assertIn("No journal matches", result["error"])

    def test_an_exact_name_is_not_crowded_out_by_partial_matches(self):
        # Analytic accounts sort by name: these seven come before "Sales".
        self.env["account.analytic.account"].create([
            {"name": f"{letter} Sales", "plan_id": self.plan.id} for letter in "ABCDEFG"])
        result, _label = self.run_tool("pnl", dict(PNL, analytic_account="sales"))
        self.assertEqual(self.revenue_of(result), 1000.0)

    def test_the_names_find_gives_are_taken_back(self):
        """A display name can add a code in brackets or a partner, which a
        search by name misses: find gives the plain name and the code, and
        either resolves even beside a longer name containing it."""
        self.sales_dept.write({"code": "100", "partner_id": self.partner_a.id})
        self.env["account.analytic.account"].create({"name": "Sales Europe", "code": "200", "plan_id": self.plan.id})
        self.assertEqual(self.sales_dept.display_name, f"[100] Sales - {self.partner_a.name}")
        result, _label = self.run_tool("find", {"kind": "analytic_account", "query": "Sales"})
        self.assertEqual(result["matches"], [
            {"name": "Sales", "code": "100", "plan": "Departments"},
            {"name": "Sales Europe", "code": "200", "plan": "Departments"},
        ])
        for name in ("Sales", "100"):
            result, _label = self.run_tool("pnl", dict(PNL, analytic_account=name))
            self.assertEqual(self.revenue_of(result), 1000.0, name)
        result, _label = self.run_tool("pnl", dict(PNL, analytic_account="Sal"))
        self.assertIn("Sales (code 100); Sales Europe (code 200)", result["error"])
        self.assertIn("or give its code", result["error"])
        # Partners come back as the books carry them: not their contacts.
        self.env["res.partner"].create({"name": "Ann", "parent_id": self.partner_a.id})
        result, _label = self.run_tool("find", {"kind": "partner", "query": self.partner_a.name})
        self.assertEqual(result["matches"], [{"name": self.partner_a.name}])

    def test_budget_against_actual(self):
        budget = self.env["odin.pnl.budget"].create({
            "name": "FY2026", "date_from": "2026-01-01", "date_to": "2026-12-31",
            "line_ids": [Command.create({"account_id": self.revenue.id, "date": "2026-09-01",
                                         "balance": -1600.0})],
        })
        result, _label = self.run_tool("pnl", dict(PNL, budget="auto"))
        self.assertEqual(result["budget"], budget.name)
        row = next(row for row in result["rows"] if row["code"] == "REV")
        self.assertEqual(row["budget"]["Sep 2026"], 1600.0)
        self.assertEqual(row["variance"]["Sep 2026"], -200.0, "actual minus budget, computed for the model")
        self.assertEqual(row["budget_pct"]["Sep 2026"], 87.5)
        self.assertEqual(self.run_tool("pnl", dict(PNL, budget="FY2026"))[0]["budget"], "FY2026")
        self.assertIn("error", self.run_tool("pnl", dict(PNL, budget="Missing"))[0])

    def test_split_by_analytic_plan(self):
        result, label = self.run_tool("balances", dict(
            PNL, account_code=None, line_code="REV", group_by="analytic", plan="Departments"))
        self.assertEqual({row["name"]: row["values"]["Sep 2026"] for row in result["rows"]},
                         {"Sales": 1000.0, "Product": 400.0})
        self.assertEqual(label, "Splitting by analytic")

    def test_an_older_conversation_keeps_working_without_the_new_arguments(self):
        """A conversation keeps the tools it started with: their calls lack
        arguments added since, which default to none."""
        old_call = {"date_from": "2026-09-01", "date_to": "2026-09-30", "comparison": "none", "line_code": None}
        self.assertEqual(self.revenue_of(self.run_tool("pnl", old_call)[0]), 1400.0)

    # ------------------------------------------------------------------
    # Receivables and payables
    # ------------------------------------------------------------------

    def open_item(self, partner, amount, days_overdue, kind="receivable"):
        """A posted invoice-like entry: ``amount`` owed on the partner's
        receivable (or payable) account, due ``days_overdue`` days ago."""
        today = fields.Date.context_today(self.env["odin.ai.tools"].with_user(self.accountant))
        account = self.company_data["default_account_receivable" if kind == "receivable" else "default_account_payable"]
        sign = 1 if kind == "receivable" else -1
        move = self.env["account.move"].create({
            "move_type": "entry",
            "date": today - timedelta(days=max(days_overdue, 0) + 5),
            "journal_id": self.misc.id,
            "line_ids": [
                Command.create({"account_id": account.id, "partner_id": partner.id, "name": "open",
                                "debit": max(sign * amount, 0.0), "credit": max(-sign * amount, 0.0),
                                "date_maturity": today - timedelta(days=days_overdue)}),
                Command.create({"account_id": self.revenue.id, "name": "open",
                                "debit": max(-sign * amount, 0.0), "credit": max(sign * amount, 0.0)}),
            ],
        })
        move.action_post()
        return move

    def test_receivables_aged_by_partner(self):
        self.open_item(self.partner_a, 300.0, 45)
        self.open_item(self.partner_a, 200.0, -10)
        self.open_item(self.partner_b, 50.0, 100)
        result, label = self.run_tool("open_items", {"kind": "receivable", "group_by": "partner", "partner": ""})
        rows = {row["partner"]: row for row in result["rows"]}
        self.assertEqual(rows[self.partner_a.display_name]["total"], 500.0)
        self.assertEqual(rows[self.partner_a.display_name]["days_31_60"], 300.0)
        self.assertEqual(rows[self.partner_a.display_name]["not_due"], 200.0)
        self.assertEqual(rows[self.partner_b.display_name]["over_90"], 50.0)
        self.assertEqual(result["total"], 550.0)
        self.assertEqual(label, "Ageing receivables")
        # The partner's chip opens that partner's open items.
        action = self.conversation.open_evidence(rows[self.partner_a.display_name]["ref"])
        self.assertEqual(action["res_model"], "account.move.line")
        items = self.env["account.move.line"].search(action["domain"])
        self.assertEqual(sum(items.mapped("amount_residual")), 500.0)

    def test_open_items_by_age_and_by_document(self):
        self.open_item(self.partner_a, 300.0, 45)
        late = self.open_item(self.partner_b, 50.0, 100)
        self.open_item(self.partner_b, 80.0, 10, kind="payable")
        result, _label = self.run_tool("open_items", {"kind": "receivable", "group_by": "bucket", "partner": ""})
        self.assertEqual(result["buckets"]["days_31_60"], 300.0)
        self.assertEqual(result["buckets"]["over_90"], 50.0)
        result, _label = self.run_tool("open_items", {"kind": "receivable", "group_by": "document", "partner": ""})
        self.assertEqual(result["documents"][0]["number"], late.name, "the most overdue first")
        self.assertEqual(result["documents"][0]["days_overdue"], 100)
        result, _label = self.run_tool("open_items", {"kind": "payable", "group_by": "bucket", "partner": ""})
        self.assertEqual(result["buckets"]["days_1_30"], 80.0, "what we owe, positive")
        result, _label = self.run_tool("open_items", {
            "kind": "receivable", "group_by": "partner", "partner": self.partner_b.name})
        self.assertEqual([row["partner"] for row in result["rows"]], [self.partner_b.display_name])

    # ------------------------------------------------------------------
    # Entries, retention, the page's first look
    # ------------------------------------------------------------------

    def test_an_entry_number_used_in_two_companies(self):
        other = self.setup_other_company()
        journal = self.env["account.journal"].create(
            {"name": "Twin misc", "code": "TWIN", "type": "general", "company_id": other["company"].id})
        here = self.post("2026-09-20", [(self.opex, 10.0)])
        there = self.env["account.move"].create({
            "move_type": "entry", "company_id": other["company"].id, "journal_id": journal.id, "date": "2026-09-21",
            "line_ids": [
                Command.create({"account_id": other["default_account_revenue"].id, "credit": 5.0, "name": "x"}),
                Command.create({"account_id": other["default_account_expense"].id, "debit": 5.0, "name": "x"}),
            ],
        })
        there.action_post()
        self.env.cr.execute("UPDATE account_move SET name = %s WHERE id = %s", [here.name, there.id])
        there.invalidate_recordset(["name"])
        self.accountant.write({"company_ids": [Command.link(other["company"].id)]})
        tools = self.env["odin.ai.tools"].with_user(self.accountant).with_context(
            allowed_company_ids=[self.env.company.id, other["company"].id])
        result, _label = tools.run(self.conversation, "entry", {"number": here.name})
        self.assertEqual(result["company"], self.env.company.name)
        self.assertEqual(result["same_number_in"], [other["company"].name])

    def test_retention_removes_untouched_conversations(self):
        Conversation = self.env["odin.ai.conversation"].with_user(self.accountant)
        old = Conversation.browse(Conversation.ask_start("Old?"))
        self.env.cr.execute("UPDATE odin_ai_conversation SET write_date = now() - interval '200 days' WHERE id = %s",
                            [old.id])
        self.env["odin.ai.conversation"].invalidate_model(["write_date"])
        self.env["odin.ai.run"]._cron_purge()
        self.assertFalse(old.exists())
        self.assertTrue(self.conversation.exists(), "a recent conversation stays")

    def test_the_page_says_what_to_set_up(self):
        Conversation = self.env["odin.ai.conversation"]
        boot = Conversation.with_user(self.accountant).ask_bootstrap()
        self.assertFalse(boot["setup"])
        self.assertFalse(boot["can_configure"])
        self.env["odin.ai.run"].sudo().create({
            "company_id": self.env.company.id, "feature": "ask", "status": "ok", "input_tokens": 2500000})
        boot = Conversation.with_user(self.accountant).ask_bootstrap()
        self.assertIn("% of this month's AI budget", boot["budget_warning"])
        self.env.company.odin_ai_consent = False
        boot = Conversation.with_user(self.accountant).ask_bootstrap()
        self.assertEqual(boot["setup"], "consent")
        self.assertTrue(boot["ready"])
        admin = new_test_user(self.env, login="ai_admin", groups="base.group_system,account.group_account_user",
                              company_id=self.env.company.id, company_ids=[self.env.company.id])
        self.assertTrue(Conversation.with_user(admin).ask_bootstrap()["can_configure"])

    def test_cached_input_counts_at_its_price(self):
        run = self.env["odin.ai.run"].sudo().create({
            "company_id": self.env.company.id, "feature": "ask", "status": "ok",
            "input_tokens": 1000, "output_tokens": 500, "cache_read_tokens": 100000, "cache_write_tokens": 2000})
        self.assertEqual(run.total_tokens, 103500)
        self.assertEqual(run.budget_tokens, 1000 + 500 + 2500 + 10000)
