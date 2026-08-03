# -*- coding: utf-8 -*-
"""Model-wide access switches (``access.manager.model.rule``).

One line per (profile, model).  It gathers the coarse, model-level toggles:
the create/edit/delete/duplicate/archive/import/export buttons, a "make the
whole model read-only" switch, the chatter sub-buttons, and the reports and
contextual actions to remove from the model's *Action* menu.
"""

from odoo import _, api, fields, models


class AccessModelRule(models.Model):
    _name = "access.manager.model.rule"
    _inherit = "access.manager.cache.mixin"
    _description = "Access Manager - Model Rule"
    _rec_name = "model_id"

    profile_id = fields.Many2one(
        "access.manager.profile", required=True, ondelete="cascade", index=True)
    model_id = fields.Many2one(
        "ir.model", string="Model", required=True, ondelete="cascade",
        domain="[('transient', '=', False)]",
        help="The model these restrictions apply to, e.g. Sales Order. "
             "One line per model, per profile.")
    # Stored so the cache builder and the ``_search`` fast-path can group rules
    # by technical model name without dereferencing ``model_id`` per record.
    model_name = fields.Char(
        related="model_id.model", store=True, index=True, string="Model Name")

    # Toolbar / action-menu switches (applied to the view arch).
    hide_create = fields.Boolean(
        string="Hide Create",
        help="Removes the New button. Creating is still blocked server-side, "
             "so it cannot be worked around from the API.")
    hide_edit = fields.Boolean(
        string="Hide Edit",
        help="Records open read-only: no field can be changed and the save "
             "indicator never appears.")
    hide_delete = fields.Boolean(
        string="Hide Delete",
        help="Removes Delete from the Action menu and from the list's "
             "multi-select toolbar.")
    hide_duplicate = fields.Boolean(
        string="Hide Duplicate",
        help="Removes Duplicate from the Action menu of the form view.")
    hide_archive = fields.Boolean(
        string="Hide Archive / Unarchive",
        help="Removes Archive and Unarchive, and blocks any change to the "
             "active flag server-side - including a direct field edit.")
    hide_import = fields.Boolean(
        string="Hide Import",
        help="Removes the Import records link from the list view of this model.")
    hide_export = fields.Boolean(
        string="Hide Export",
        help="Removes the Export action and blocks server-side export "
             "(XML-RPC / URL) for this model.")
    readonly = fields.Boolean(
        string="Make Model Read-Only",
        help="Every field of every view of this model becomes read-only for "
             "the targeted users. Implies no create, edit or delete.")

    # View-type (view switcher) restrictions. The primary list/form views are
    # never removed so an action always has a landing view.
    hide_kanban = fields.Boolean(
        string="Hide Kanban View",
        help="Removes this view from the switcher in the top-right corner. "
             "List and form are never removed, so every menu keeps a landing "
             "view.")
    hide_pivot = fields.Boolean(
        string="Hide Pivot View", help="Removes the pivot (analysis) view.")
    hide_graph = fields.Boolean(
        string="Hide Graph View", help="Removes the graph (chart) view.")
    hide_calendar = fields.Boolean(
        string="Hide Calendar View", help="Removes the calendar view.")
    hide_activity_view = fields.Boolean(
        string="Hide Activity View",
        help="Removes the activity (to-do grid) view. This is the view type, "
             "not the chatter's Activities button.")
    hide_map = fields.Boolean(
        string="Hide Map View", help="Removes the map view.")

    # Search-view extras (applied globally-per-model in the client).
    hide_spreadsheet = fields.Boolean(
        string="Hide 'Insert in Spreadsheet'",
        help="Removes Insert in Spreadsheet from list and pivot views, a "
             "common way of exporting data around an export restriction.")
    hide_favourites = fields.Boolean(
        string="Hide Favorites",
        help="Removes the Favorites menu from the search bar, so saved "
             "filters cannot be created or shared on this model.")

    # Chatter switches (applied to the form arch).
    hide_chatter = fields.Boolean(
        string="Hide Chatter",
        help="Removes the whole right-hand panel: messages, log notes, "
             "activities and followers.")
    hide_send_message = fields.Boolean(
        string="Hide 'Send message'",
        help="Keeps the chatter but removes the Send message button, so the "
             "user cannot email the customer from the record.")
    hide_log_note = fields.Boolean(
        string="Hide 'Log note'",
        help="Keeps the chatter but removes the Log note button.")
    hide_activity = fields.Boolean(
        string="Hide 'Activities'",
        help="Keeps the chatter but removes the Activities button, so the "
             "user cannot schedule follow-ups on this model.")
    hide_followers = fields.Boolean(
        string="Hide Followers",
        help="Keeps the chatter but hides the followers avatars and the "
             "Add followers button.")

    # Reports and contextual actions removed from the model's Action menu.
    hidden_report_ids = fields.Many2many(
        "ir.actions.report", "access_model_rule_report_rel", "rule_id", "report_id",
        string="Hidden Reports", domain="[('model', '=', model_name)]",
        help="Reports removed from the Print menu. Only reports belonging to "
             "the model above are offered.")
    hidden_action_ids = fields.Many2many(
        "ir.actions.act_window", "access_model_rule_action_rel", "rule_id", "action_id",
        string="Hidden Actions",
        domain="[('binding_model_id.model', '=', model_name)]",
        help="Entries removed from the Action (gear) menu, e.g. a mass-update "
             "or a related-records shortcut.")

    # Reading a row of a dozen disabled checkboxes is slower than reading a
    # sentence, so the list shows this instead and keeps the toggles in the
    # form dialog where they are grouped and labelled.
    restriction_summary = fields.Char(
        compute="_compute_restriction_summary", string="Restrictions",
        help="Plain-language recap of the switches set on this line. Open the "
             "line to change them.")

    _model_uniq = models.Constraint(
        "UNIQUE (profile_id, model_id)",
        "This model already has a rule line in this profile.",
    )

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

    @api.depends("readonly", "hide_create", "hide_edit", "hide_delete",
                 "hide_duplicate", "hide_archive", "hide_import", "hide_export",
                 "hide_chatter", "hide_send_message", "hide_log_note",
                 "hide_activity", "hide_followers", "hide_spreadsheet",
                 "hide_favourites", "hide_kanban", "hide_pivot", "hide_graph",
                 "hide_calendar", "hide_activity_view", "hide_map",
                 "hidden_report_ids", "hidden_action_ids")
    def _compute_restriction_summary(self):
        # Switch -> wording, in reading order. Built per call so the labels are
        # translated for the reader rather than at import time.
        labels = (
            ("readonly", _("read-only")),
            ("hide_create", _("no create")),
            ("hide_edit", _("no edit")),
            ("hide_delete", _("no delete")),
            ("hide_duplicate", _("no duplicate")),
            ("hide_archive", _("no archive")),
            ("hide_import", _("no import")),
            ("hide_export", _("no export")),
            ("hide_chatter", _("no chatter")),
            ("hide_spreadsheet", _("no spreadsheet")),
            ("hide_favourites", _("no favorites")),
        )
        for rule in self:
            parts = [label for flag, label in labels if rule[flag]]
            if not rule.hide_chatter:
                chatter = [label for flag, label in (
                    ("hide_send_message", _("send message")),
                    ("hide_log_note", _("log note")),
                    ("hide_activity", _("activities")),
                    ("hide_followers", _("followers")),
                ) if rule[flag]]
                if chatter:
                    parts.append(_("no %(items)s", items=", ".join(chatter)))
            modes = rule._hidden_view_modes()
            if modes:
                parts.append(_("hides %(views)s view",
                               views=", ".join(sorted(modes))))
            if rule.hidden_report_ids:
                parts.append(_("%(count)s reports hidden",
                               count=len(rule.hidden_report_ids)))
            if rule.hidden_action_ids:
                parts.append(_("%(count)s actions hidden",
                               count=len(rule.hidden_action_ids)))
            rule.restriction_summary = ", ".join(parts) or _(
                "Nothing restricted yet — open this line to choose")

    @api.onchange("model_id")
    def _onchange_model_id(self):
        # Reports and actions are model-scoped; drop stale selections.
        self.hidden_report_ids = False
        self.hidden_action_ids = False
