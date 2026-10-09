# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models
from odoo.exceptions import UserError


class OdinCommitment(models.Model):
    """Money a project has promised to spend but has not yet been billed for.

    The missing leg of budget-vs-actual. A project that is on budget against
    actuals and has 400k of unbilled purchase orders open is not on budget, and
    Odoo Community has nothing that says so.

    One purchase order line can produce SEVERAL commitments — one per analytic
    account in its distribution — because that is how the cost actually splits.
    """

    _name = "odin.commitment"
    _description = "Cost Commitment"
    _order = "date desc, id desc"
    _rec_name = "name"

    name = fields.Char(required=True, index=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        help="Always the company currency. A commitment is compared against a "
        "budget, so it is converted on the way in rather than at read time.",
    )
    analytic_account_id = fields.Many2one(
        "account.analytic.account", required=True, index=True, ondelete="restrict"
    )
    project_id = fields.Many2one("project.project", index=True)
    partner_id = fields.Many2one("res.partner", string="Vendor", index=True)
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    description = fields.Char()

    purchase_line_id = fields.Many2one(
        "purchase.order.line", index="btree_not_null", ondelete="cascade", copy=False
    )
    purchase_order_id = fields.Many2one(
        related="purchase_line_id.order_id", store=True, index="btree_not_null"
    )
    is_manual = fields.Boolean(
        compute="_compute_is_manual",
        store=True,
        help="A commitment recorded by hand — a signed subcontract that has not "
        "been turned into a purchase order yet, for instance.",
    )
    distribution_percent = fields.Float(
        default=100.0,
        digits=(16, 4),
        help="Share of the source line that falls on this analytic account.",
    )

    amount_manual = fields.Monetary(
        string="Committed (manual)",
        help="Used only when there is no purchase order line behind this.",
    )
    amount_manual_invoiced = fields.Monetary(string="Invoiced (manual)")

    amount_committed = fields.Monetary(compute="_compute_amounts", store=True)
    amount_invoiced = fields.Monetary(compute="_compute_amounts", store=True)
    amount_open = fields.Monetary(
        compute="_compute_amounts",
        store=True,
        help="Committed but not yet billed. This is what a budget has to carry.",
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        index=True,
    )

    _distribution_sane = models.Constraint(
        "CHECK(distribution_percent > 0 AND distribution_percent <= 100)",
        "The analytic share must be between 0 and 100 percent.",
    )

    @api.depends("purchase_line_id")
    def _compute_is_manual(self):
        for commitment in self:
            commitment.is_manual = not commitment.purchase_line_id

    @api.depends(
        "purchase_line_id.price_subtotal",
        "purchase_line_id.product_qty",
        "purchase_line_id.qty_invoiced",
        "purchase_line_id.order_id.currency_id",
        "purchase_line_id.order_id.date_order",
        "purchase_line_id.order_id.state",
        "distribution_percent",
        "amount_manual",
        "amount_manual_invoiced",
        "currency_id",
        "state",
    )
    def _compute_amounts(self):
        for commitment in self:
            share = (commitment.distribution_percent or 100.0) / 100.0
            line = commitment.purchase_line_id
            if line:
                order = line.order_id
                ordered = line.price_subtotal * share
                invoiced = (
                    line.price_subtotal * (line.qty_invoiced / line.product_qty) * share
                    if line.product_qty
                    else 0.0
                )
                if order.currency_id and order.currency_id != commitment.currency_id:
                    rate_date = order.date_order or fields.Date.context_today(commitment)
                    ordered = order.currency_id._convert(
                        ordered,
                        commitment.currency_id,
                        commitment.company_id,
                        rate_date,
                    )
                    invoiced = order.currency_id._convert(
                        invoiced,
                        commitment.currency_id,
                        commitment.company_id,
                        rate_date,
                    )
            else:
                ordered = commitment.amount_manual
                invoiced = commitment.amount_manual_invoiced

            rounding = commitment.currency_id.round
            commitment.amount_committed = rounding(ordered)
            commitment.amount_invoiced = rounding(invoiced)
            # A cancelled or closed commitment carries no open exposure, however
            # the source order still looks.
            commitment.amount_open = (
                0.0
                if commitment.state in ("cancelled", "closed")
                else rounding(max(0.0, ordered - invoiced))
            )

    # ── Keeping budgets current ─────────────────────────────────────────────
    #
    # A budget line's committed and actual figures cannot depend on commitments
    # and analytic lines through @api.depends — the dependency crosses an
    # aggregate over two other models, and recomputing every budget whenever any
    # analytic line moves would be far too expensive. So the figures are
    # refreshed from the events that actually change them.

    def _refresh_budget_lines(self):
        accounts = self.analytic_account_id
        if not accounts:
            return
        lines = self.env["odin.cost.budget.line"].search(
            [("analytic_account_id", "in", accounts.ids)]
        )
        if lines:
            lines._compute_consumption()

    @api.model_create_multi
    def create(self, vals_list):
        commitments = super().create(vals_list)
        commitments._refresh_budget_lines()
        return commitments

    def write(self, vals):
        res = super().write(vals)
        if {"state", "amount_manual", "amount_manual_invoiced", "distribution_percent",
                "analytic_account_id"} & set(vals):
            self._refresh_budget_lines()
        return res

    def unlink(self):
        accounts = self.analytic_account_id
        res = super().unlink()
        lines = self.env["odin.cost.budget.line"].search(
            [("analytic_account_id", "in", accounts.ids)]
        )
        if lines:
            lines._compute_consumption()
        return res

    def action_open(self):
        self.write({"state": "open"})
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True

    def action_cancel(self):
        for commitment in self:
            if commitment.purchase_line_id:
                raise UserError(
                    self.env._(
                        "This commitment follows %(order)s. Cancel the purchase "
                        "order rather than the commitment, or the two stop agreeing.",
                        order=commitment.purchase_order_id.display_name,
                    )
                )
        self.write({"state": "cancelled"})
        return True

    def action_view_source(self):
        self.ensure_one()
        if not self.purchase_order_id:
            raise UserError(self.env._("This commitment was recorded by hand."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "res_id": self.purchase_order_id.id,
            "view_mode": "form",
        }

    @api.model
    def _cron_close_settled(self):
        """Close commitments whose order is fully billed or cancelled.

        A safety net, not the primary mechanism: the amounts are computed from
        the order line, so they stay right either way. This is about keeping the
        open list readable.
        """
        open_commitments = self.search([("state", "=", "open")])
        to_close = open_commitments.filtered(
            lambda c: c.purchase_line_id
            and (
                c.purchase_order_id.state in ("cancel",)
                or c.currency_id.is_zero(c.amount_open)
            )
        )
        to_close.write({"state": "closed"})
        return True
