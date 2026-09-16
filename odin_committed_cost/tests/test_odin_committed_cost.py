# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestOdinCommittedCost(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["odin.cost.budget"])
        cls.vendor = cls.env["res.partner"].create({"name": "Civil Subcontractor"})

        # Two plans, because that is what makes committed cost easy to get wrong.
        cls.plan_project = cls.env["account.analytic.plan"].create({"name": "Projects"})
        cls.plan_dept = cls.env["account.analytic.plan"].create({"name": "Departments"})
        cls.aa_civil = cls.env["account.analytic.account"].create(
            {"name": "Civil Works", "plan_id": cls.plan_project.id}
        )
        cls.aa_electrical = cls.env["account.analytic.account"].create(
            {"name": "Electrical", "plan_id": cls.plan_project.id}
        )
        cls.aa_dept = cls.env["account.analytic.account"].create(
            {"name": "Operations", "plan_id": cls.plan_dept.id}
        )
        cls.env.company.odin_cost_plan_id = cls.plan_project

        cls.project = cls.env["project.project"].create(
            {"name": "Solar Plant B", "account_id": cls.aa_civil.id}
        )

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _po(self, distribution, qty=10, price=1000.0):
        po = self.env["purchase.order"].create(
            {
                "partner_id": self.vendor.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.product_a.id,
                            "name": "Civil works",
                            "product_qty": qty,
                            "price_unit": price,
                            "date_planned": fields.Datetime.now(),
                            "analytic_distribution": distribution,
                            "taxes_id": [Command.clear()],
                        }
                    )
                ],
            }
        )
        return po

    def _budget(self, lines=None, state="confirmed"):
        budget = self.env["odin.cost.budget"].create(
            {
                "project_id": self.project.id,
                "date_from": self.today.replace(month=1, day=1),
                "date_to": self.today.replace(month=12, day=31),
                "line_ids": lines
                or [
                    Command.create(
                        {
                            "name": "Civil Works",
                            "analytic_account_id": self.aa_civil.id,
                            "amount_budget": 50000.0,
                        }
                    )
                ],
            }
        )
        if state == "confirmed":
            budget.action_confirm()
        return budget

    # ── Commitment generation ───────────────────────────────────────────────
    def test_confirming_a_po_creates_a_commitment(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        commitments = po.odin_commitment_ids
        self.assertEqual(len(commitments), 1)
        commitment = commitments[0]
        self.assertEqual(commitment.analytic_account_id, self.aa_civil)
        self.assertEqual(commitment.state, "open")
        self.assertAlmostEqual(commitment.amount_committed, 10000.0, places=2)
        self.assertAlmostEqual(commitment.amount_open, 10000.0, places=2)

    def test_a_split_distribution_splits_the_commitment(self):
        po = self._po(
            {str(self.aa_civil.id): 60.0, str(self.aa_electrical.id): 40.0}
        )
        po.button_confirm()
        by_account = {
            c.analytic_account_id: c.amount_committed for c in po.odin_commitment_ids
        }
        self.assertEqual(len(by_account), 2)
        self.assertAlmostEqual(by_account[self.aa_civil], 6000.0, places=2)
        self.assertAlmostEqual(by_account[self.aa_electrical], 4000.0, places=2)

    def test_multi_plan_key_is_counted_once(self):
        """A key naming two plans is two dimensions of one cost, not a split."""
        key = f"{self.aa_civil.id},{self.aa_dept.id}"
        po = self._po({key: 100.0})
        po.button_confirm()
        self.assertEqual(len(po.odin_commitment_ids), 1)
        commitment = po.odin_commitment_ids[0]
        self.assertEqual(commitment.analytic_account_id, self.aa_civil)
        self.assertAlmostEqual(commitment.amount_committed, 10000.0, places=2)

    def test_commitment_follows_a_changed_order(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        po.order_line.product_qty = 15
        self.assertAlmostEqual(
            po.odin_commitment_ids[0].amount_committed, 15000.0, places=2
        )

    def test_confirming_twice_does_not_duplicate(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        po._odin_sync_commitments()
        self.assertEqual(len(po.odin_commitment_ids), 1)

    def test_cancelling_the_order_closes_the_commitment(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        po.button_cancel()
        self.assertEqual(po.odin_commitment_ids[0].state, "closed")
        self.assertAlmostEqual(po.odin_commitment_ids[0].amount_open, 0.0, places=2)

    def test_a_po_commitment_cannot_be_cancelled_by_hand(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        with self.assertRaises(UserError):
            po.odin_commitment_ids[0].action_cancel()

    # ── Billing moves committed to actual ───────────────────────────────────
    def _bill_po(self, po, qty=None):
        po.order_line.qty_received = qty if qty is not None else po.order_line.product_qty
        action = po.action_create_invoice()
        bill = self.env["account.move"].browse(action["res_id"])
        bill.invoice_date = self.today
        bill.action_post()
        return bill

    def test_billing_reduces_the_open_commitment(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        commitment = po.odin_commitment_ids[0]
        self.assertAlmostEqual(commitment.amount_open, 10000.0, places=2)
        self._bill_po(po)
        self.assertAlmostEqual(commitment.amount_invoiced, 10000.0, places=2)
        self.assertAlmostEqual(commitment.amount_open, 0.0, places=2)
        self.assertEqual(commitment.state, "closed")

    def test_partial_billing_leaves_the_balance_committed(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        self._bill_po(po, qty=4)
        commitment = po.odin_commitment_ids[0]
        self.assertAlmostEqual(commitment.amount_invoiced, 4000.0, places=2)
        self.assertAlmostEqual(commitment.amount_open, 6000.0, places=2)
        self.assertEqual(commitment.state, "open")

    # ── Budget roll-up ──────────────────────────────────────────────────────
    def test_budget_line_picks_up_commitments(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        budget = self._budget()
        budget.action_refresh()
        line = budget.line_ids[0]
        self.assertAlmostEqual(line.amount_committed, 10000.0, places=2)
        self.assertAlmostEqual(line.amount_remaining, 40000.0, places=2)
        self.assertAlmostEqual(line.percent_consumed, 20.0, places=2)
        self.assertFalse(line.is_overrun)

    def test_budget_counts_committed_and_actual_without_double_counting(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        self._bill_po(po, qty=4)
        budget = self._budget()
        budget.action_refresh()
        line = budget.line_ids[0]
        # 4,000 billed became actual; 6,000 is still committed. Total consumed
        # must be 10,000, not 14,000.
        self.assertAlmostEqual(line.amount_committed, 6000.0, places=2)
        self.assertAlmostEqual(line.amount_actual, 4000.0, places=2)
        self.assertAlmostEqual(line.amount_consumed, 10000.0, places=2)

    def test_overrun_is_flagged(self):
        po = self._po({str(self.aa_civil.id): 100.0}, qty=100)
        po.button_confirm()
        budget = self._budget()
        budget.action_refresh()
        self.assertTrue(budget.line_ids[0].is_overrun)
        self.assertTrue(budget.is_overrun)
        self.assertLess(budget.amount_remaining, 0)

    def test_revenue_is_not_counted_as_negative_cost(self):
        """A customer invoice on the project's analytic account is revenue."""
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.vendor.id,
                "invoice_date": self.today,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Progress claim",
                            "quantity": 1,
                            "price_unit": 80000.0,
                            "analytic_distribution": {str(self.aa_civil.id): 100.0},
                            "tax_ids": [Command.clear()],
                        }
                    )
                ],
            }
        )
        invoice.action_post()
        budget = self._budget()
        budget.action_refresh()
        self.assertAlmostEqual(budget.line_ids[0].amount_actual, 0.0, places=2)

    # ── Budget lifecycle ────────────────────────────────────────────────────
    def test_cannot_confirm_an_empty_budget(self):
        budget = self.env["odin.cost.budget"].create(
            {
                "project_id": self.project.id,
                "date_from": self.today,
                "date_to": self.today,
            }
        )
        with self.assertRaises(UserError):
            budget.action_confirm()

    def test_revision_supersedes_and_copies_lines(self):
        budget = self._budget()
        action = budget.action_create_revision()
        revision = self.env["odin.cost.budget"].browse(action["res_id"])
        self.assertEqual(budget.state, "revised")
        self.assertEqual(revision.state, "draft")
        self.assertEqual(revision.revision_of_id, budget)
        self.assertEqual(revision.revision_number, 1)
        self.assertEqual(len(revision.line_ids), len(budget.line_ids))

    def test_only_confirmed_budgets_reach_the_report(self):
        draft = self._budget(state="draft")
        rows = self.env["odin.cost.control.report"].search(
            [("kind", "=", "budget"), ("project_id", "=", self.project.id)]
        )
        self.assertFalse(rows)
        draft.action_confirm()
        rows = self.env["odin.cost.control.report"].search(
            [("kind", "=", "budget"), ("project_id", "=", self.project.id)]
        )
        self.assertTrue(rows)

    def test_cost_control_report_shows_all_three_legs(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        self._bill_po(po, qty=4)
        self._budget()
        kinds = set(
            self.env["odin.cost.control.report"]
            .search([("analytic_account_id", "=", self.aa_civil.id)])
            .mapped("kind")
        )
        self.assertEqual(kinds, {"budget", "committed", "actual"})

    def test_project_totals(self):
        po = self._po({str(self.aa_civil.id): 100.0})
        po.button_confirm()
        self._budget().action_refresh()
        self.assertEqual(self.project.odin_budget_count, 1)
        self.assertAlmostEqual(self.project.odin_amount_budget, 50000.0, places=2)
        self.assertAlmostEqual(self.project.odin_amount_committed, 10000.0, places=2)
        self.assertAlmostEqual(self.project.odin_amount_remaining, 40000.0, places=2)

    # ── Manual commitments ──────────────────────────────────────────────────
    def test_manual_commitment_counts_toward_the_budget(self):
        manual = self.env["odin.commitment"].create(
            {
                "name": "Signed subcontract SC-01",
                "analytic_account_id": self.aa_civil.id,
                "project_id": self.project.id,
                "partner_id": self.vendor.id,
                "amount_manual": 7500.0,
                "state": "open",
            }
        )
        self.assertTrue(manual.is_manual)
        self.assertAlmostEqual(manual.amount_open, 7500.0, places=2)
        budget = self._budget()
        budget.action_refresh()
        self.assertAlmostEqual(budget.line_ids[0].amount_committed, 7500.0, places=2)
