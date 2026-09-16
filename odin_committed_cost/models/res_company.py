# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    odin_cost_plan_id = fields.Many2one(
        "account.analytic.plan",
        string="Cost Control Analytic Plan",
        help="The analytic plan cost control measures on. Leave empty to use "
        "the plan of each project's own analytic account.",
    )

    def _odin_cost_plan(self, project=None):
        """Which analytic plan commitments are recorded against.

        This matters more than it looks. An analytic distribution key can name
        SEVERAL accounts — "5,12" means 100% on account 5 in one plan and 100%
        on account 12 in another, because they are different dimensions of the
        same cost, not a split of it. Recording a commitment for each would
        double the committed figure. So exactly one plan drives cost control.
        """
        self.ensure_one()
        if self.odin_cost_plan_id:
            return self.odin_cost_plan_id
        if project and project.account_id:
            return project.account_id.root_plan_id
        return self.env["account.analytic.plan"].browse()
