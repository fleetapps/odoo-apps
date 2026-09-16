# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)

from odoo import api, fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    odin_ld_agreement_ids = fields.One2many(
        "odin.ld.agreement",
        "project_id",
        string="Liquidated Damages",
    )
    odin_ld_agreement_count = fields.Integer(compute="_compute_odin_ld_totals")
    odin_ld_exposure = fields.Monetary(
        string="LD Exposure",
        compute="_compute_odin_ld_totals",
        currency_field="currency_id",
        help="Damages accrued against us on this project, net of what has already "
        "been deducted. Damages we are recovering from subcontractors are not "
        "netted off — they are a different counterparty's liability.",
    )

    @api.depends(
        "odin_ld_agreement_ids.amount_accrued",
        "odin_ld_agreement_ids.amount_charged",
        "odin_ld_agreement_ids.direction",
        "odin_ld_agreement_ids.state",
    )
    def _compute_odin_ld_totals(self):
        for project in self:
            agreements = project.odin_ld_agreement_ids
            project.odin_ld_agreement_count = len(agreements)
            live = agreements.filtered(
                lambda a: a.direction == "payable"
                and a.state in ("active", "completed")
            )
            project.odin_ld_exposure = sum(live.mapped("amount_accrued")) - sum(
                live.mapped("amount_charged")
            )

    def action_view_odin_ld_agreements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Liquidated Damages"),
            "res_model": "odin.ld.agreement",
            "view_mode": "list,form",
            "domain": [("project_id", "=", self.id)],
            "context": {
                "default_project_id": self.id,
                "default_partner_id": self.partner_id.id,
            },
        }
