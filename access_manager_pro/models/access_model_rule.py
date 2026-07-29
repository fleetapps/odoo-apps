# -*- coding: utf-8 -*-
"""Model-wide access switches (``access.manager.model.rule``).

One line per (profile, model).  It gathers the coarse, model-level toggles:
the create/edit/delete/duplicate/archive/import/export buttons, a "make the
whole model read-only" switch, the chatter sub-buttons, and the reports and
contextual actions to remove from the model's *Action* menu.
"""

from odoo import api, fields, models


class AccessModelRule(models.Model):
    _name = "access.manager.model.rule"
    _inherit = "access.manager.cache.mixin"
    _description = "Access Manager - Model Rule"
    _rec_name = "model_id"

    profile_id = fields.Many2one(
        "access.manager.profile", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one(
        "ir.model", string="Model", required=True, ondelete="cascade",
        domain="[('transient', '=', False)]")
    # Stored so the cache builder and the ``_search`` fast-path can group rules
    # by technical model name without dereferencing ``model_id`` per record.
    model_name = fields.Char(
        related="model_id.model", store=True, index=True, string="Model Name")

    # Toolbar / action-menu switches (applied to the view arch).
    hide_create = fields.Boolean(string="Hide Create")
    hide_edit = fields.Boolean(string="Hide Edit")
    hide_delete = fields.Boolean(string="Hide Delete")
    hide_duplicate = fields.Boolean(string="Hide Duplicate")
    hide_archive = fields.Boolean(string="Hide Archive / Unarchive")
    hide_import = fields.Boolean(string="Hide Import")
    hide_export = fields.Boolean(
        string="Hide Export",
        help="Removes the Export action and blocks server-side export "
             "(XML-RPC / URL) for this model.")
    readonly = fields.Boolean(
        string="Make Model Read-Only",
        help="Every field of every view of this model becomes read-only for "
             "the targeted users.")

    # View-type (view switcher) restrictions. The primary list/form views are
    # never removed so an action always has a landing view.
    hide_kanban = fields.Boolean(string="Hide Kanban View")
    hide_pivot = fields.Boolean(string="Hide Pivot View")
    hide_graph = fields.Boolean(string="Hide Graph View")
    hide_calendar = fields.Boolean(string="Hide Calendar View")
    hide_activity_view = fields.Boolean(string="Hide Activity View")
    hide_map = fields.Boolean(string="Hide Map View")

    # Search-view extras (applied globally-per-model in the client).
    hide_spreadsheet = fields.Boolean(string="Hide 'Insert in Spreadsheet'")
    hide_favourites = fields.Boolean(string="Hide Favorites")

    # Chatter switches (applied to the form arch).
    hide_chatter = fields.Boolean(string="Hide Chatter")
    hide_send_message = fields.Boolean(string="Hide 'Send message'")
    hide_log_note = fields.Boolean(string="Hide 'Log note'")
    hide_activity = fields.Boolean(string="Hide 'Activities'")
    hide_followers = fields.Boolean(string="Hide Followers")

    # Reports and contextual actions removed from the model's Action menu.
    hidden_report_ids = fields.Many2many(
        "ir.actions.report", "access_model_rule_report_rel", "rule_id", "report_id",
        string="Hidden Reports", domain="[('model', '=', model_name)]")
    hidden_action_ids = fields.Many2many(
        "ir.actions.act_window", "access_model_rule_action_rel", "rule_id", "action_id",
        string="Hidden Actions",
        domain="[('binding_model_id.model', '=', model_name)]")

    _sql_constraints = [
        ("model_uniq", "unique(profile_id, model_id)",
         "This model already has a rule line in this profile."),
    ]

    # Maps the boolean switches to Odoo view-mode tokens.
    _VIEW_MODE_MAP = {
        "hide_kanban": "kanban",
        "hide_pivot": "pivot",
        "hide_graph": "graph",
        "hide_calendar": "calendar",
        "hide_activity_view": "activity",
        "hide_map": "map",
    }

    def _hidden_view_modes(self):
        self.ensure_one()
        return {mode for flag, mode in self._VIEW_MODE_MAP.items() if self[flag]}

    @api.onchange("model_id")
    def _onchange_model_id(self):
        # Reports and actions are model-scoped; drop stale selections.
        self.hidden_report_ids = False
        self.hidden_action_ids = False
