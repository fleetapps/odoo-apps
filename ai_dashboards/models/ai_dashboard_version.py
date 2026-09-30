# -*- coding: utf-8 -*-
"""Immutable spec history.

An assistant that can edit your reports needs an undo. Specs are a few hundred
bytes, so every version is kept rather than a rolling window - the cheapest
safety net in the product.

Rows are never updated. Reverting writes a *new* version whose payload happens
to match an old one, so the history stays an append-only account of what
actually happened rather than something that can be rewritten.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class AIDashboardVersion(models.Model):
    _name = "ai.dashboard.version"
    _description = "AI Dashboard Version"
    _order = "create_date desc, id desc"
    _rec_name = "note"

    dashboard_id = fields.Many2one(
        "ai.dashboard", required=True, ondelete="cascade", index=True)
    spec_json = fields.Text(required=True, readonly=True)
    author_id = fields.Many2one("res.users", readonly=True, ondelete="set null")
    note = fields.Char(
        readonly=True,
        help="What changed, in the words of whoever (or whatever) changed it.")
    is_current = fields.Boolean(compute="_compute_is_current")

    @api.depends("spec_json", "dashboard_id.spec_json")
    def _compute_is_current(self):
        for rec in self:
            rec.is_current = rec.spec_json == rec.dashboard_id.spec_json

    def write(self, vals):
        raise UserError(_(
            "History cannot be edited. Restore a version instead — that adds a "
            "new entry and leaves the record of what happened intact."))

    def action_restore(self):
        """Put this version back, as a new version."""
        self.ensure_one()
        dashboard = self.dashboard_id
        if not self.env.su and not self.env.user.has_group(
                "ai_dashboards.group_dashboard_admin") \
                and dashboard.owner_id.id != self.env.uid:
            raise AccessError(_(
                "Only %s can restore a version of this dashboard.")
                % dashboard.owner_id.name)
        dashboard.write({
            "spec_json": self.spec_json,
            "_version_note": _("Restored the version from %s")
            % fields.Datetime.to_string(self.create_date),
        })
        return dashboard.action_open()
