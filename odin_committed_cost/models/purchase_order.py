# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    odin_commitment_ids = fields.One2many(
        "odin.commitment", "purchase_order_id", string="Commitments"
    )
    odin_commitment_count = fields.Integer(compute="_compute_odin_commitment_count")

    @api.depends("odin_commitment_ids")
    def _compute_odin_commitment_count(self):
        for order in self:
            order.odin_commitment_count = len(order.odin_commitment_ids)

    def button_confirm(self):
        res = super().button_confirm()
        self._odin_sync_commitments()
        return res

    def button_cancel(self):
        res = super().button_cancel()
        self.odin_commitment_ids.write({"state": "closed"})
        return res

    def _odin_sync_commitments(self):
        """Create the commitments a confirmed order implies.

        Amounts are not copied — they are computed from the order line — so an
        order that changes after confirmation keeps its commitment right without
        anything having to re-run.
        """
        Commitment = self.env["odin.commitment"]
        for order in self:
            if order.state not in ("purchase", "done"):
                continue
            plan = order.company_id._odin_cost_plan(order.project_id_for_cost())
            existing = order.odin_commitment_ids
            for line in order.order_line:
                if line.display_type:
                    continue
                for account, percent in line._odin_cost_accounts(plan):
                    already = existing.filtered(
                        lambda c, ln=line, acc=account: c.purchase_line_id == ln
                        and c.analytic_account_id == acc
                    )
                    if already:
                        already.filtered(lambda c: c.state == "draft").action_open()
                        continue
                    Commitment.create(
                        {
                            "name": order.name,
                            "description": line.name,
                            "company_id": order.company_id.id,
                            "currency_id": order.company_id.currency_id.id,
                            "analytic_account_id": account.id,
                            "project_id": order.project_id_for_cost().id or False,
                            "partner_id": order.partner_id.id,
                            "date": (order.date_order or fields.Datetime.now()).date(),
                            "purchase_line_id": line.id,
                            "distribution_percent": percent,
                            "state": "open",
                        }
                    )
        return True

    def project_id_for_cost(self):
        """The project a purchase belongs to, where one can be determined.

        Core purchase has no project link, so this reads it back from the
        analytic accounts on the lines. Returns an empty recordset when the
        order spans several projects — better no answer than the wrong one.
        """
        self.ensure_one()
        accounts = self.order_line.mapped("distribution_analytic_account_ids")
        projects = self.env["project.project"].search(
            [("account_id", "in", accounts.ids)]
        )
        return projects if len(projects) == 1 else self.env["project.project"].browse()

    def action_view_odin_commitments(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Commitments"),
            "res_model": "odin.commitment",
            "view_mode": "list,form",
            "domain": [("purchase_order_id", "=", self.id)],
        }


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _odin_cost_accounts(self, plan):
        """Yield (analytic account, percent) pairs for the cost control plan.

        A distribution key can name several accounts across different plans.
        Only the ones on `plan` are returned, so a cost is counted once. With no
        plan configured, the first account of each key is used — arbitrary, but
        consistent, and it never doubles a figure.
        """
        self.ensure_one()
        distribution = self.analytic_distribution or {}
        for key, percent in distribution.items():
            ids = [int(part) for part in str(key).split(",") if str(part).isdigit()]
            if not ids:
                continue
            accounts = self.env["account.analytic.account"].browse(ids).exists()
            if plan:
                accounts = accounts.filtered(lambda a: a.root_plan_id == plan)
            else:
                accounts = accounts[:1]
            for account in accounts:
                yield account, percent
