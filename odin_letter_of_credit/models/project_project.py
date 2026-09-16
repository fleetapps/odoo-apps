# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    odin_lc_ids = fields.One2many(
        "odin.letter.of.credit", "project_id", string="Letters of Credit"
    )
    odin_lc_count = fields.Integer(compute="_compute_odin_lc_totals")
    odin_lc_exposure = fields.Monetary(
        string="LC Exposure",
        compute="_compute_odin_lc_totals",
        currency_field="currency_id",
        help="Undrawn availability on live import credits — money the project "
        "has committed but not yet paid out.",
    )

    @api.depends(
        "odin_lc_ids.amount_available",
        "odin_lc_ids.state",
        "odin_lc_ids.direction",
    )
    def _compute_odin_lc_totals(self):
        for project in self:
            lcs = project.odin_lc_ids
            project.odin_lc_count = len(lcs)
            live = lcs.filtered(
                lambda lc: lc.direction == "import"
                and lc.state in ("issued", "advised")
            )
            project.odin_lc_exposure = sum(live.mapped("amount_available"))

    def action_view_odin_lc(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Letters of Credit"),
            "res_model": "odin.letter.of.credit",
            "view_mode": "list,form",
            "domain": [("project_id", "=", self.id)],
            "context": {"default_project_id": self.id},
        }
