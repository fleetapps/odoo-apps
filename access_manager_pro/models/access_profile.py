# -*- coding: utf-8 -*-
"""Access Manager Pro - central configuration model and rule engine.

An :class:`AccessProfile` record is the single point of configuration.  Each
profile targets a set of users, groups and/or companies and carries the rules
that describe what those targets may see and do (menus, models, fields, view
elements, record domains and a handful of user-level switches).

Every request that renders a view or touches the ORM asks this model for the
*compiled* configuration of the current user.  To keep that path cheap, the
compilation is served from a registry-level cache
(:meth:`_get_access_config`) keyed on ``(user, company)`` and returns a plain,
read-only data structure - never a recordset - so it is safe to memoise.

The cache is invalidated whenever any profile or rule line changes (see
:class:`~odoo.addons.access_manager_pro.models.access_mixin`).

References
----------
* ORM ....... https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html
* Security .. https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html
"""

import base64
import json
import logging
from datetime import timedelta

import pytz

from odoo import SUPERUSER_ID, _, api, fields, models, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Context flag set while the engine compiles its own configuration.  It lets
# the ORM/view overrides short-circuit so that reading the configuration can
# never recurse into itself (a profile ``search`` would otherwise re-enter the
# engine while it is still building the very cache it needs).
SKIP_KEY = "access_manager_skip"

# Group that is never restricted by this module (anti-lockout).
ADMIN_GROUP = "access_manager_pro.group_access_manager_admin"


class AccessProfile(models.Model):
    _name = "access.manager.profile"
    _inherit = "access.manager.cache.mixin"
    _description = "Access Manager Profile"
    _order = "sequence, name"

    name = fields.Char(
        required=True, translate=True,
        help="Name this after the people it covers and what it takes away, "
             "e.g. 'Warehouse staff - no pricing'. It is never shown to the "
             "restricted users.")
    active = fields.Boolean(
        default=True,
        help="Archiving a profile lifts every restriction it carries, "
             "immediately and for everyone it targets.")
    sequence = fields.Integer(
        default=10,
        help="Order in which profiles are listed. When several profiles target "
             "the same user their restrictions add up, so the order is "
             "presentation only - it never decides which one wins.")
    note = fields.Text(help="Free notes for administrators. Not shown to end users.")

    # --- Targeting -----------------------------------------------------------
    user_ids = fields.Many2many(
        "res.users", "access_profile_user_rel", "profile_id", "user_id",
        string="Users",
        help="Individual users this profile restricts. Use Groups instead when "
             "the rule really belongs to a role - new joiners are then covered "
             "automatically.")
    group_ids = fields.Many2many(
        "res.groups", "access_profile_group_rel", "profile_id", "group_id",
        string="Groups",
        help="Every user of these groups is restricted, including users who "
             "get the group indirectly through another one. This is the way to "
             "restrict a whole role at once.")
    company_ids = fields.Many2many(
        "res.company", "access_profile_company_rel", "profile_id", "company_id",
        string="Companies",
        help="Leave empty to apply in every company. When set, the profile only "
             "applies while one of these companies is the active company.")

    # --- Menus ---------------------------------------------------------------
    hidden_menu_ids = fields.Many2many(
        "ir.ui.menu", "access_profile_menu_rel", "profile_id", "menu_id",
        string="Hidden Menus",
        help="Selected menus and all of their sub-menus are removed from the "
             "navigation of the targeted users.")
    hide_apps_menu = fields.Boolean(
        string="Hide 'Apps' Main Menu",
        help="Remove the top-level Apps/Settings developer menus from the "
             "targeted users.")

    # --- Rule lines ----------------------------------------------------------
    model_rule_ids = fields.One2many(
        "access.manager.model.rule", "profile_id", string="Model Rules",
        help="Whole-model restrictions: buttons, view types, chatter, reports.")
    field_rule_ids = fields.One2many(
        "access.manager.field.rule", "profile_id", string="Field Rules",
        help="Per-field restrictions: hide, lock, require or mask a field.")
    element_rule_ids = fields.One2many(
        "access.manager.element.rule", "profile_id", string="View Element Rules",
        help="Single buttons, notebook tabs, search filters and group-by "
             "options to hide or disable.")
    domain_rule_ids = fields.One2many(
        "access.manager.domain.rule", "profile_id", string="Record (Domain) Rules",
        help="Which records the user may see and touch, e.g. only their own.")

    # --- User-level switches -------------------------------------------------
    is_readonly_user = fields.Boolean(
        string="Read-Only User",
        help="Targeted users may read data but cannot create, edit or delete "
             "any record (enforced server-side).")
    disable_developer_mode = fields.Boolean(
        help="Force the developer/debug mode off for the targeted users.")
    disable_login = fields.Boolean(
        help="Prevent the targeted users from logging in at all. Administrators "
             "of this app are never blocked.")

    # --- Scheduling / expiration / auto-revocation ---------------------------
    date_start = fields.Datetime(
        string="Active From",
        help="The profile is ignored before this moment. Empty = always.")
    date_end = fields.Datetime(
        string="Expires On",
        help="The profile stops applying after this moment and is "
             "auto-revoked (deactivated) by a scheduled action.")
    restriction_time_based = fields.Boolean(
        string="Time-Based",
        help="Apply the profile only during a daily time window.")
    time_start = fields.Float(
        string="From (hour)", default=8.0,
        help="Start of the daily window (0-24) in the timezone below.")
    time_end = fields.Float(
        string="To (hour)", default=18.0,
        help="End of the daily window. If earlier than the start, the window "
             "spans midnight (e.g. 22:00 -> 06:00).")
    tz = fields.Selection(
        lambda self: self._tz_selection(), string="Timezone",
        help="Timezone used to evaluate the daily window. Defaults to the "
             "user's timezone when empty.")

    # Stored so the dashboard can group/filter without a Python pass.
    restriction_type = fields.Selection(
        [("user", "User"), ("group", "Group"), ("mixed", "User & Group"),
         ("none", "Unassigned")],
        compute="_compute_restriction_type", store=True, string="Applies Via",
        help="How this profile reaches people: named users, whole groups, or "
             "both. 'Unassigned' means it targets nobody and does nothing.")
    state = fields.Selection(
        [("scheduled", "Scheduled"), ("active", "Active"),
         ("expiring", "Expiring"), ("expired", "Expired"),
         ("inactive", "Inactive")],
        compute="_compute_state", string="Status",
        help="Scheduled: starts later. Active: applying now. Expiring: ends "
             "within 7 days. Expired: no longer applying, awaiting "
             "auto-archive. Inactive: archived, restricts nothing.")

    # --- Form helpers (presentation only, never stored) ----------------------
    summary = fields.Char(
        compute="_compute_summary", string="Effect",
        help="Plain-language recap of everything this profile currently does.")
    recent_model_id = fields.Many2one(
        "ir.model", compute="_compute_recent_model_id", string="Last Model Used",
        help="Technical field: the model of the most recently added rule line. "
             "New rule lines default to it so the model is not re-picked for "
             "every line of the same model.")

    # --- Global (all-model) switches -----------------------------------------
    hide_chatter = fields.Boolean(
        string="Hide Chatter",
        help="Removes the whole chatter panel everywhere - messages, notes, "
             "activities and followers, on every model.")
    hide_send_message = fields.Boolean(
        string="Hide 'Send message'",
        help="Keeps the chatter everywhere but removes the Send message "
             "button, so this user can never email a contact from a record.")
    hide_log_note = fields.Boolean(
        string="Hide 'Log note'",
        help="Keeps the chatter everywhere but removes the Log note button.")
    hide_activity = fields.Boolean(
        string="Hide 'Activities'",
        help="Keeps the chatter everywhere but removes the Activities button, "
             "so this user cannot schedule follow-ups on any model.")
    hide_followers = fields.Boolean(
        string="Hide Followers",
        help="Keeps the chatter everywhere but hides the followers avatars "
             "and the Add followers button.")
    hide_import = fields.Boolean(
        string="Hide Import",
        help="Removes the Import records link from every list view.")
    hide_export = fields.Boolean(
        string="Hide Export",
        help="Removes Export everywhere and blocks it server-side, so it "
             "cannot be reached over XML-RPC or by URL either.")
    hide_add_property = fields.Boolean(
        string="Hide 'Add a Property'",
        help="Removes the Add a Property button, which would otherwise let "
             "the user add custom fields to records.")
    hide_search_panel = fields.Boolean(
        string="Hide Search Panel",
        help="Removes the left-hand filter panel of list and kanban views.")
    hide_spreadsheet = fields.Boolean(
        string="Hide 'Insert in Spreadsheet'",
        help="Removes Insert in Spreadsheet everywhere. Worth pairing with "
             "Hide Export - it is the usual way around an export restriction.")
    hide_favourites = fields.Boolean(
        string="Hide Favorites",
        help="Removes the Favorites menu from the search bar on every model.")

    # Odoo 19 no longer reads ``_sql_constraints`` (it only logs "no longer
    # supported" and builds the table objects from ``models.Constraint``).
    # The database identifier stays ``access_manager_profile_name_uniq``.
    _name_uniq = models.Constraint(
        "UNIQUE (name)",
        "An access profile with this name already exists.",
    )

    # ------------------------------------------------------------------ #
    #  Targeting helpers
    # ------------------------------------------------------------------ #
    @api.model
    def _user_group_ids(self, user):
        """Return the group ids of ``user`` in a version-safe way.

        Odoo 19 exposes ``_get_group_ids`` (groups are resolved through the new
        role mechanism); 17 and 18 rely on the plain ``groups_id`` m2m.
        """
        if hasattr(user, "_get_group_ids"):
            return user._get_group_ids()
        return user.groups_id.ids

    @api.model
    def _group_user_ids(self, groups):
        """Return the ids of the users targeted through ``groups``.

        The reverse of :meth:`_user_group_ids`, so it must count *implied*
        membership too: a user of a group that implies one of ``groups`` is
        matched by ``_profiles_for`` and is therefore genuinely restricted.
        Odoo 19 renamed ``res.groups.users`` to ``user_ids`` and added
        ``all_user_ids`` (implied membership included); 17 and 18 only have the
        direct ``users`` m2m.
        """
        field = "all_user_ids" if "all_user_ids" in groups._fields else "users"
        return set(groups.mapped(field).ids)

    @api.model
    def _tz_selection(self):
        return [(tz, tz) for tz in pytz.all_timezones]

    @api.depends("user_ids", "group_ids")
    def _compute_restriction_type(self):
        for profile in self:
            has_user, has_group = bool(profile.user_ids), bool(profile.group_ids)
            profile.restriction_type = (
                "mixed" if has_user and has_group else
                "user" if has_user else
                "group" if has_group else "none")

    @api.depends("active", "date_start", "date_end")
    def _compute_state(self):
        now = fields.Datetime.now()
        soon = now + timedelta(days=7)
        for profile in self:
            if not profile.active:
                profile.state = "inactive"
            elif profile.date_end and profile.date_end < now:
                profile.state = "expired"
            elif profile.date_start and profile.date_start > now:
                profile.state = "scheduled"
            elif profile.date_end and profile.date_end <= soon:
                profile.state = "expiring"
            else:
                profile.state = "active"

    @api.depends("is_readonly_user", "disable_login", "hidden_menu_ids",
                 "hide_apps_menu", "model_rule_ids", "field_rule_ids",
                 "element_rule_ids", "domain_rule_ids", "date_end",
                 "restriction_time_based")
    def _compute_summary(self):
        """One line telling an administrator what this profile actually does.

        Reading eight tabs to answer "what does this profile restrict?" is the
        single biggest friction in a rule editor, so the answer is kept at the
        top of the form and updates as the profile is edited.
        """
        for profile in self:
            parts = []
            if profile.is_readonly_user:
                parts.append(_("read-only user"))
            if profile.disable_login:
                parts.append(_("login blocked"))
            if profile.hide_apps_menu:
                parts.append(_("Apps menu hidden"))
            if profile.hidden_menu_ids:
                parts.append(_("%(count)s menus", count=len(profile.hidden_menu_ids)))
            if profile.model_rule_ids:
                parts.append(_("%(count)s models", count=len(profile.model_rule_ids)))
            if profile.field_rule_ids:
                parts.append(_("%(count)s fields", count=len(profile.field_rule_ids)))
            if profile.element_rule_ids:
                parts.append(_("%(count)s buttons/tabs",
                               count=len(profile.element_rule_ids)))
            if profile.domain_rule_ids:
                parts.append(_("%(count)s record rules",
                               count=len(profile.domain_rule_ids)))
            if profile.restriction_time_based:
                parts.append(_("daily time window"))
            if profile.date_end:
                parts.append(_("expires %(date)s",
                               date=fields.Date.to_string(profile.date_end)))
            profile.summary = " · ".join(parts)

    @api.depends("model_rule_ids.model_id", "field_rule_ids.model_id",
                 "element_rule_ids.model_id", "domain_rule_ids.model_id")
    def _compute_recent_model_id(self):
        """The model to pre-select on the next rule line.

        Rules are almost always written in bursts against one model (hide three
        fields and two buttons on Sales Orders), so re-picking the model on
        every line is pure friction.
        """
        for profile in self:
            recent = self.env["ir.model"]
            for lines in (profile.domain_rule_ids, profile.element_rule_ids,
                          profile.field_rule_ids, profile.model_rule_ids):
                if lines and lines[-1].model_id:
                    recent = lines[-1].model_id
                    break
            profile.recent_model_id = recent

    def _within_time_window(self, profile, now_utc):
        """Whether ``now_utc`` falls in the profile's daily time window."""
        tz_name = profile.tz or self.env.user.tz or "UTC"
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.UTC
        local = pytz.utc.localize(now_utc).astimezone(tz)
        hour = local.hour + local.minute / 60.0
        start, end = profile.time_start, profile.time_end
        if start == end:
            return True
        if start < end:
            return start <= hour <= end
        return hour >= start or hour <= end  # window spanning midnight

    def _profiles_for(self, user, company):
        """Profiles that apply to ``user`` in ``company`` *right now*.

        Filters on company scope and the scheduling window (active-from,
        expires-on and the optional daily time window). Inactive profiles are
        already excluded by the default ``active`` filter of ``search``.
        """
        now = fields.Datetime.now()
        profiles = self.sudo().with_context(**{SKIP_KEY: True}).search([
            "|",
            ("user_ids", "in", user.id),
            ("group_ids", "in", self._user_group_ids(user)),
        ])

        def _applies(p):
            if p.company_ids and company not in p.company_ids:
                return False
            if p.date_start and p.date_start > now:
                return False
            if p.date_end and p.date_end < now:
                return False
            if p.restriction_time_based and not self._within_time_window(p, now):
                return False
            return True

        return profiles.filtered(_applies)

    @api.model
    def _cron_revoke_expired(self):
        """Deactivate profiles whose expiry has passed (auto-revocation)."""
        now = fields.Datetime.now()
        expired = self.sudo().with_context(**{SKIP_KEY: True}).search([
            ("active", "=", True),
            ("date_end", "!=", False),
            ("date_end", "<", now),
        ])
        if expired:
            expired.write({"active": False})  # clears cache via the mixin
        return True

    @api.model
    def action_cleanup_expired(self):
        """Public entry point for the dashboard's 'Cleanup Expired' button."""
        self._cron_revoke_expired()
        return True

    # ------------------------------------------------------------------ #
    #  JSON import / export
    # ------------------------------------------------------------------ #
    # Fields serialised for each rule model, keyed by the o2m field name.
    _EXPORT_RULE_FIELDS = {
        "model_rule_ids": [
            "hide_create", "hide_edit", "hide_delete", "hide_duplicate",
            "hide_archive", "hide_import", "hide_export", "readonly",
            "hide_chatter", "hide_send_message", "hide_log_note",
            "hide_activity", "hide_followers", "hide_kanban", "hide_pivot",
            "hide_graph", "hide_calendar", "hide_activity_view", "hide_map",
            "hide_spreadsheet", "hide_favourites",
        ],
        "field_rule_ids": [
            "field_name", "mode", "no_create", "no_open", "condition",
            "mask_char", "mask_show_last",
        ],
        "element_rule_ids": ["element_type", "selector", "mode", "condition"],
        "domain_rule_ids": [
            "name", "domain", "perm_read", "perm_create", "perm_write",
            "perm_unlink", "is_soft", "use_hierarchy", "hierarchy_field_name",
            "hierarchy_mode",
        ],
    }
    _EXPORT_PROFILE_FIELDS = [
        "name", "active", "sequence", "note", "hide_apps_menu",
        "is_readonly_user", "disable_developer_mode", "disable_login",
        "restriction_time_based", "time_start", "time_end", "tz",
        "hide_chatter", "hide_send_message", "hide_log_note", "hide_activity",
        "hide_followers", "hide_import", "hide_export", "hide_add_property",
        "hide_search_panel", "hide_spreadsheet", "hide_favourites",
    ]

    def _serialize(self):
        """Return a JSON-serialisable representation of the profiles in ``self``.

        References are stored by durable natural keys (model/field technical
        names, user logins, group and report/action XML ids) so a bundle can be
        moved between databases and versions. Anything that cannot be resolved
        on import is skipped and logged, mirroring how cross-version rule
        migration is expected to behave.
        """
        profiles = []
        for profile in self:
            data = {f: profile[f] for f in self._EXPORT_PROFILE_FIELDS}
            data["users"] = profile.user_ids.mapped("login")
            data["groups"] = [g.get_external_id().get(g.id) or g.full_name
                              for g in profile.group_ids]
            data["companies"] = profile.company_ids.mapped("name")
            data["menus"] = [m.get_external_id().get(m.id) for m in profile.hidden_menu_ids]
            for o2m, field_names in self._EXPORT_RULE_FIELDS.items():
                rows = []
                for rule in profile[o2m]:
                    row = {f: rule[f] for f in field_names}
                    if "model_name" in rule._fields:
                        row["model"] = rule.model_name
                    if o2m == "model_rule_ids":
                        row["reports"] = [r.get_external_id().get(r.id)
                                          for r in rule.hidden_report_ids]
                        row["actions"] = [a.get_external_id().get(a.id)
                                          for a in rule.hidden_action_ids]
                    rows.append(row)
                data[o2m] = rows
            profiles.append(data)
        return {"version": 1, "module": "access_manager_pro", "profiles": profiles}

    def action_export_json(self):
        """Download the selected profiles as a JSON bundle."""
        records = self or self.search([])
        payload = json.dumps(records._serialize(), indent=2, default=str)
        attachment = self.env["ir.attachment"].create({
            "name": "access_profiles_%s.json" % fields.Date.today(),
            "type": "binary",
            "datas": base64.b64encode(payload.encode()),
            "mimetype": "application/json",
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%d?download=true" % attachment.id,
            "target": "self",
        }

    @api.model
    def _import_bundle(self, payload):
        """Create profiles from a JSON bundle (string or dict). Returns the
        created recordset; unresolved references are skipped and logged."""
        data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
        if not isinstance(data, dict) or "profiles" not in data:
            raise UserError(_("This file is not an Access Manager profile bundle."))
        created = self.browse()
        for pdata in data["profiles"]:
            created |= self._import_one(pdata)
        return created

    @api.model
    def _import_one(self, pdata):
        vals = {f: pdata.get(f) for f in self._EXPORT_PROFILE_FIELDS if f in pdata}
        vals["name"] = self._unique_name(pdata.get("name") or "Imported Profile")
        vals["user_ids"] = [(6, 0, self.env["res.users"].search(
            [("login", "in", pdata.get("users", []))]).ids)]
        vals["group_ids"] = [(6, 0, self._resolve_refs(pdata.get("groups", []), "res.groups"))]
        vals["company_ids"] = [(6, 0, self.env["res.company"].search(
            [("name", "in", pdata.get("companies", []))]).ids)]
        vals["hidden_menu_ids"] = [(6, 0, self._resolve_refs(pdata.get("menus", []), "ir.ui.menu"))]
        for o2m in self._EXPORT_RULE_FIELDS:
            vals[o2m] = [(0, 0, line) for line in self._import_rule_lines(o2m, pdata.get(o2m, []))]
        return self.create(vals)

    @api.model
    def _import_rule_lines(self, o2m, rows):
        lines = []
        for row in rows:
            line = {k: v for k, v in row.items()
                    if k in self._EXPORT_RULE_FIELDS[o2m]}
            model_name = row.get("model")
            if model_name:
                model = self.env["ir.model"].sudo()._get(model_name)
                if not model:
                    _logger.warning("Access Manager import: unknown model %r skipped", model_name)
                    continue
                line["model_id"] = model.id
            if o2m == "field_rule_ids" and row.get("field_name"):
                field = self.env["ir.model.fields"].sudo()._get(model_name, row["field_name"])
                if not field:
                    _logger.warning("Access Manager import: unknown field %r on %r", row["field_name"], model_name)
                    continue
                line["field_id"] = field.id
            if o2m == "domain_rule_ids" and row.get("hierarchy_field_name"):
                field = self.env["ir.model.fields"].sudo()._get(model_name, row["hierarchy_field_name"])
                if field:
                    line["hierarchy_field_id"] = field.id
            if o2m == "model_rule_ids":
                line["hidden_report_ids"] = [(6, 0, self._resolve_refs(row.get("reports", []), "ir.actions.report"))]
                line["hidden_action_ids"] = [(6, 0, self._resolve_refs(row.get("actions", []), "ir.actions.act_window"))]
            lines.append(line)
        return lines

    @api.model
    def _resolve_refs(self, xmlids, model):
        ids = []
        for xmlid in xmlids:
            if not xmlid:
                continue
            record = self.env.ref(xmlid, raise_if_not_found=False)
            if record and record._name == model:
                ids.append(record.id)
        return ids

    @api.model
    def _unique_name(self, name):
        candidate, i = name, 1
        while self.search_count([("name", "=", candidate)], limit=1):
            i += 1
            candidate = "%s (%d)" % (name, i)
        return candidate

    def _login_disabled_for(self, user):
        """Whether ``user`` is blocked from logging in by any active profile.

        Evaluated without company scoping (login happens before a company is
        chosen) and with a hard anti-lockout guard: the superuser, this app's
        administrators and settings administrators can never be locked out.
        """
        group_ids = set(self._user_group_ids(user))
        safe_groups = (
            self.env.ref(ADMIN_GROUP, raise_if_not_found=False),
            self.env.ref("base.group_system", raise_if_not_found=False),
        )
        if user.id == SUPERUSER_ID or any(g and g.id in group_ids for g in safe_groups):
            return False
        now = fields.Datetime.now()
        return bool(self.sudo().with_context(**{SKIP_KEY: True}).search_count([
            ("active", "=", True),
            ("disable_login", "=", True),
            "|", ("date_start", "=", False), ("date_start", "<=", now),
            "|", ("date_end", "=", False), ("date_end", ">=", now),
            "|", ("user_ids", "in", user.id), ("group_ids", "in", list(group_ids)),
        ], limit=1))

    # ------------------------------------------------------------------ #
    #  Compiled configuration (cached)
    # ------------------------------------------------------------------ #
    @api.model
    def _empty_config(self):
        return {
            "restricted": False,
            "is_readonly_user": False,
            "disable_developer_mode": False,
            "menus": {"hidden": frozenset(), "hide_apps": False},
            "globals": {},
            "models": {},          # model_name -> {"fields", "elements", "switches"}
            "domains": {},         # model_name -> tuple(domain rule dicts)
            "domain_models": frozenset(),
            "masked_models": frozenset(),
            "reports": {},         # model_name -> frozenset(report ids)
            "actions": {},         # model_name -> frozenset(action ids)
            "view_modes": {},      # model_name -> frozenset(hidden view-mode tokens)
            "view_mode_models": frozenset(),
        }

    @api.model
    @tools.ormcache("self.env.uid", "self.env.company.id")
    def _get_access_config(self):
        """Return the read-only, cached access configuration for the user.

        Superusers and Access Manager administrators are never restricted, which
        keeps the hot path free and prevents any chance of an admin locking
        themselves out.
        """
        config = self._empty_config()
        user = self.env.user
        if self.env.su or user.has_group(ADMIN_GROUP):
            return config

        profiles = self._profiles_for(user, self.env.company)
        if not profiles:
            return config

        globals_ = {
            "hide_chatter": False, "hide_send_message": False,
            "hide_log_note": False, "hide_activity": False,
            "hide_followers": False, "hide_import": False, "hide_export": False,
            "hide_add_property": False, "hide_search_panel": False,
            "hide_spreadsheet": False, "hide_favourites": False,
        }
        hidden_menus = set()
        hide_apps = False
        models_cfg = {}
        domains = {}
        reports = {}
        actions = {}
        view_modes = {}

        for profile in profiles:
            config["restricted"] = True
            config["is_readonly_user"] |= profile.is_readonly_user
            config["disable_developer_mode"] |= profile.disable_developer_mode
            hide_apps |= profile.hide_apps_menu
            hidden_menus.update(profile.hidden_menu_ids.ids)
            for key in globals_:
                globals_[key] |= profile[key]

            for rule in profile.model_rule_ids:
                cfg = models_cfg.setdefault(rule.model_name, self._blank_model_cfg())
                self._merge_switches(cfg["switches"], rule)
                if rule.hidden_report_ids:
                    reports.setdefault(rule.model_name, set()).update(
                        rule.hidden_report_ids.ids)
                if rule.hidden_action_ids:
                    actions.setdefault(rule.model_name, set()).update(
                        rule.hidden_action_ids.ids)
                hidden_modes = rule._hidden_view_modes()
                if hidden_modes:
                    view_modes.setdefault(rule.model_name, set()).update(hidden_modes)

            for rule in profile.field_rule_ids:
                cfg = models_cfg.setdefault(rule.model_name, self._blank_model_cfg())
                cfg["fields"].append({
                    "field": rule.field_name,
                    "mode": rule.mode,
                    "no_create": rule.no_create,
                    "no_open": rule.no_open,
                    "condition": (rule.condition or "").strip(),
                    "mask_char": rule.mask_char or "•",
                    "mask_show_last": max(0, rule.mask_show_last or 0),
                })

            for rule in profile.element_rule_ids:
                cfg = models_cfg.setdefault(rule.model_name, self._blank_model_cfg())
                cfg["elements"].append({
                    "type": rule.element_type,
                    "selector": rule.selector,
                    "mode": rule.mode,
                    "condition": (rule.condition or "").strip(),
                })

            for rule in profile.domain_rule_ids:
                domains.setdefault(rule.model_name, []).append({
                    "name": rule.name,
                    "domain": self._domain_for_rule(rule, user),
                    "read": rule.perm_read,
                    "create": rule.perm_create,
                    "write": rule.perm_write,
                    "unlink": rule.perm_unlink,
                    "soft": rule.is_soft,
                })

        # Freeze the mutable containers so callers cannot corrupt the cache.
        config["menus"] = {"hidden": frozenset(hidden_menus), "hide_apps": hide_apps}
        config["globals"] = globals_
        config["models"] = {
            name: {
                "fields": tuple(cfg["fields"]),
                "elements": tuple(cfg["elements"]),
                "switches": cfg["switches"],
            }
            for name, cfg in models_cfg.items()
        }
        config["domains"] = {name: tuple(rules) for name, rules in domains.items()}
        config["domain_models"] = frozenset(domains)
        config["masked_models"] = frozenset(
            name for name, cfg in config["models"].items()
            if any(f["mode"] == "masked" for f in cfg["fields"]))
        config["reports"] = {name: frozenset(ids) for name, ids in reports.items()}
        config["actions"] = {name: frozenset(ids) for name, ids in actions.items()}
        config["view_modes"] = {name: frozenset(m) for name, m in view_modes.items()}
        config["view_mode_models"] = frozenset(view_modes)
        return config

    def _domain_for_rule(self, rule, user):
        """The effective restriction domain string for a domain rule.

        For a hierarchy rule the restricted set is "records NOT owned by the
        user (or their subordinates)"; the allowed user ids are resolved here,
        at per-user cache-build time, so the baked domain is already specific to
        ``user``.
        """
        if not rule.use_hierarchy or not rule.hierarchy_field_name:
            return rule.domain or "[]"
        allowed = self._hierarchy_user_ids(user, rule.hierarchy_mode)
        return repr([(rule.hierarchy_field_name, "not in", list(allowed))])

    def _hierarchy_user_ids(self, user, mode):
        """User ids the ``user`` may act on: themselves and, optionally, the
        users managed below them in the employee hierarchy."""
        allowed = {user.id}
        if mode != "subordinates" or "hr.employee" not in self.env:
            return allowed
        employee = self.env["hr.employee"].sudo().with_context(
            **{SKIP_KEY: True}, active_test=False)
        mine = employee.search([("user_id", "=", user.id)])
        if mine:
            subordinates = employee.search([("id", "child_of", mine.ids)])
            allowed |= set(subordinates.mapped("user_id").ids)
        return allowed

    @api.model
    def _blank_model_cfg(self):
        return {"fields": [], "elements": [], "switches": {}}

    @api.model
    def _merge_switches(self, switches, rule):
        """OR the boolean switches of ``rule`` into the ``switches`` dict."""
        for key in (
            "hide_create", "hide_edit", "hide_delete", "hide_duplicate",
            "hide_archive", "hide_export", "hide_import", "readonly",
            "hide_chatter", "hide_send_message", "hide_log_note",
            "hide_activity", "hide_followers",
            "hide_spreadsheet", "hide_favourites",
        ):
            switches[key] = switches.get(key, False) or rule[key]
