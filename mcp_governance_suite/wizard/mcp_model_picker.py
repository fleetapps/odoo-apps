# -*- coding: utf-8 -*-
"""Bulk "add models to a scope" helper.

Adding models one row at a time is the slowest part of setting a scope up, and
it is the very first thing an administrator has to do. This picker takes a
multi-select plus one preset and writes the whole set in a single step, so
granting an assistant read access to twenty models is one action rather than
twenty.

Presets exist because the useful configurations are few: read, read+draft, and
full. Offering five independent checkboxes up front invites mistakes; the
matrix is there for fine-tuning afterwards.

The actual writing lives on ``mcp.scope.add_models`` - the install hook and the
Connect screen add models too, and three copies of that logic would drift.
"""
from odoo import _, api, fields, models

# Re-exported: PRESETS moved to the scope model, but it was importable from
# here first and downstream code may still reach for it.
from ..models.mcp_scope import PRESETS  # noqa: F401


class MCPModelPicker(models.TransientModel):
    _name = "mcp.model.picker"
    _description = "Add Models to an MCP Scope"

    scope_id = fields.Many2one(
        "mcp.scope", string="Scope", required=True,
        default=lambda self: self._default_scope(),
        help="Which governance scope these models are added to.")
    model_ids = fields.Many2many(
        "ir.model", string="Models", required=True,
        domain="[('transient', '=', False)]",
        help="Pick as many as you like. Models already in this scope are "
             "skipped, so it is safe to re-run.")
    preset = fields.Selection(
        [("read", "Read only — the safe starting point"),
         ("draft", "Read, create and update — no deleting"),
         ("full", "Full access including delete")],
        default="read", required=True,
        help="Applied to every model you selected. Fine-tune individual rows "
             "afterwards in the permission matrix.")
    already_count = fields.Integer(compute="_compute_preview")
    new_count = fields.Integer(compute="_compute_preview")

    @api.model
    def _default_scope(self):
        scope = self.env["mcp.scope"].browse(
            self.env.context.get("default_scope_id") or 0).exists()
        if scope:
            return scope
        # Otherwise the read-only default, so the safe scope is pre-selected.
        return self.env["mcp.scope"].search(
            [("active", "=", True)], order="read_only desc, id", limit=1)

    def _existing_model_ids(self, scope):
        """Models already on this scope, archived rows included."""
        return scope.existing_model_ids() if scope else set()

    @api.depends("scope_id", "model_ids")
    def _compute_preview(self):
        """Tell the admin what will actually happen before they commit."""
        for rec in self:
            existing = rec._existing_model_ids(rec.scope_id)
            selected = set(rec.model_ids.ids)
            rec.already_count = len(selected & existing)
            rec.new_count = len(selected - existing)

    def action_add(self):
        self.ensure_one()
        to_add = self.scope_id.add_models(self.model_ids.mapped("model"),
                                          preset=self.preset)

        if not to_add:
            message = _("Every model you picked was already in '%s'. Nothing "
                        "to do.") % self.scope_id.name
        else:
            message = _(
                "Added %(added)s model(s) to '%(scope)s'.%(skipped)s",
                added=len(to_add), scope=self.scope_id.name,
                skipped=_(" %s were already there and were skipped.")
                % (len(self.model_ids) - len(to_add))
                if len(to_add) != len(self.model_ids) else "")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Model permissions updated"),
                "message": message,
                "type": "success" if to_add else "warning",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
