# -*- coding: utf-8 -*-
"""Access Manager Pro - profile & rule models.

Official references used:
* ORM API:            https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html
* Security reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html
* View architectures: https://www.odoo.com/documentation/19.0/developer/reference/user_interface/view_architectures.html

SECURITY DISCLAIMER: UI-level hiding is a usability layer. This module also
enforces server-side where possible (export blocking), but true data security
still requires ir.model.access / ir.rule configuration. See "Security Pitfalls"
in the security reference above.
"""
from odoo import api, fields, models


class AccessProfile(models.Model):
    _name = "access.manager.profile"
    _description = "Access Manager Profile"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    user_ids = fields.Many2many("res.users", string="Apply to Users")
    group_ids = fields.Many2many("res.groups", string="Apply to Groups")
    company_ids = fields.Many2many("res.company", string="Companies",
                                   help="Empty = all companies")
    hidden_menu_ids = fields.Many2many("ir.ui.menu", string="Hidden Menus")
    field_rule_ids = fields.One2many("access.manager.field.rule", "profile_id")
    element_rule_ids = fields.One2many("access.manager.element.rule", "profile_id")
    model_rule_ids = fields.One2many("access.manager.model.rule", "profile_id")

    @api.model
    def _applicable_profiles(self, model_name=None):
        """Profiles matching current user (direct or via group), company-aware."""
        user = self.env.user
        profiles = self.sudo().search([
            "|", ("user_ids", "in", user.id), ("group_ids", "in", user.group_ids.ids),
        ])
        profiles = profiles.filtered(
            lambda p: not p.company_ids or self.env.company in p.company_ids)
        if model_name:
            profiles = profiles.filtered(
                lambda p: p.hidden_menu_ids or any(
                    r.model_name == model_name for r in
                    (p.field_rule_ids | p.element_rule_ids | p.model_rule_ids)))
        return profiles

    def write(self, vals):
        res = super().write(vals)
        self.env.registry.clear_cache()  # menu visibility is ormcache'd
        return res


class AccessFieldRule(models.Model):
    _name = "access.manager.field.rule"
    _description = "Field-level rule"

    profile_id = fields.Many2one("access.manager.profile", required=True,
                                 ondelete="cascade")
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(related="model_id.model", store=True)
    field_id = fields.Many2one("ir.model.fields", required=True,
                               ondelete="cascade",
                               domain="[('model_id', '=', model_id)]")
    field_name = fields.Char(related="field_id.name", store=True)
    mode = fields.Selection(
        [("invisible", "Hide"), ("readonly", "Read-only"),
         ("required", "Required")], required=True, default="invisible")


class AccessElementRule(models.Model):
    """Buttons, notebook tabs, smart-buttons - addressed by name / xpath."""
    _name = "access.manager.element.rule"
    _description = "View element rule"

    profile_id = fields.Many2one("access.manager.profile", required=True,
                                 ondelete="cascade")
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(related="model_id.model", store=True)
    element_type = fields.Selection(
        [("button", "Button (by name/action)"),
         ("page", "Notebook Tab (by label)"),
         ("xpath", "Custom XPath")], required=True, default="button")
    selector = fields.Char(required=True,
        help="button: the name= attr or action id | page: the tab label | "
             "xpath: any XPath e.g. //group[@name='pricing']")
    mode = fields.Selection([("invisible", "Hide"), ("readonly", "Disable")],
                            default="invisible", required=True)


class AccessModelRule(models.Model):
    """Model-wide switches: create/edit/delete/duplicate/export/chatter."""
    _name = "access.manager.model.rule"
    _description = "Model-level rule"

    profile_id = fields.Many2one("access.manager.profile", required=True,
                                 ondelete="cascade")
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    model_name = fields.Char(related="model_id.model", store=True)
    hide_create = fields.Boolean()
    hide_edit = fields.Boolean()
    hide_delete = fields.Boolean()
    hide_duplicate = fields.Boolean()
    block_export = fields.Boolean(help="Also enforced server-side on export_data().")
    hide_chatter = fields.Boolean()
