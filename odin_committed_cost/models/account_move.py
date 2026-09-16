# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        """Billing an order consumes its commitment.

        The amounts themselves are computed from the purchase order line, so
        posting a bill moves value from committed to actual without anything
        being copied. What this does is close commitments that are now fully
        billed, so the open list stays honest.
        """
        posted = super()._post(soft=soft)
        lines = posted.invoice_line_ids.filtered("purchase_line_id")
        if lines:
            commitments = self.env["odin.commitment"].search(
                [
                    ("purchase_line_id", "in", lines.purchase_line_id.ids),
                    ("state", "=", "open"),
                ]
            )
            settled = commitments.filtered(
                lambda c: c.currency_id.is_zero(c.amount_open)
            )
            settled.write({"state": "closed"})
            # Whether or not anything closed, value has moved from committed to
            # actual, so the budgets that watch these accounts are now stale.
            commitments._refresh_budget_lines()

        # Posting any analytic-bearing document changes actuals, not just a bill
        # raised from a purchase order.
        accounts = posted.invoice_line_ids.distribution_analytic_account_ids
        if accounts:
            budget_lines = self.env["odin.cost.budget.line"].search(
                [("analytic_account_id", "in", accounts.ids)]
            )
            if budget_lines:
                budget_lines._compute_consumption()
        return posted
