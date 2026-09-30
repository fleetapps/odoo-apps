# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""Per branch/country routing: which Odoo user approves purchase requests
raised from a given FMS branch. Looked up by exact (country, branch) match
against the strings FMS sends; falls back to the instance-wide default
approver in Settings when nothing matches.

A plain list rather than free country/branch fields on the settings screen
because a large multi-country operator has many branches and this is the
kind of thing an admin edits over time without a developer -- same shape as
the ``mismatch``/``conflict`` logs in shopify_bisync, just for routing
instead of sync errors.
"""
from odoo import api, fields, models


class FmsBranchRoute(models.Model):
    _name = "fms.connector.branch.route"
    _description = "FMS Branch -> Approver Routing"
    _rec_name = "branch"

    _unique_branch_route = models.Constraint(
        "UNIQUE(country, branch, company_id)",
        "Only one route per country/branch/company.",
    )

    active = fields.Boolean(default=True)
    country = fields.Char(
        help="Must match the country string FMS sends exactly (case-"
             "insensitive). Leave the country on its own route row blank "
             "to match any country for that branch name.",
    )
    branch = fields.Char(
        required=True,
        help="Must match the branch/program-office name FMS sends exactly "
             "(case-insensitive).",
    )
    approver_id = fields.Many2one(
        "res.users", required=True, string="Approver",
        help="Set as assigned_to on purchase requests raised from this "
             "branch.",
    )
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company,
    )

    @api.model
    def resolve_approver(self, country, branch):
        """Best-match approver for a given (country, branch) pair, or the
        instance-wide fallback from Settings if nothing matches. Returns a
        ``res.users`` recordset (possibly empty)."""
        country = (country or "").strip()
        branch = (branch or "").strip()
        domain = [
            ("company_id", "=", self.env.company.id),
            ("branch", "=ilike", branch),
        ]
        route = self.search(
            domain + [("country", "=ilike", country)], limit=1
        ) or self.search(domain + [("country", "=", False)], limit=1)
        if route:
            return route.approver_id
        fallback_id = self.env["ir.config_parameter"].sudo().get_param(
            "fms_connector.default_approver_id"
        )
        return (
            self.env["res.users"].browse(int(fallback_id))
            if fallback_id else self.env["res.users"]
        )
