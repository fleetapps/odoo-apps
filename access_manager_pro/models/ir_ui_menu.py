# -*- coding: utf-8 -*-
from odoo import api, models


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _visible_menu_ids(self, debug=False):
        """Core menu-visibility hook; subtract profile-hidden menus.
        Admins of this app are never restricted (anti-lockout)."""
        visible = super()._visible_menu_ids(debug=debug)
        if self.env.su or self.env.user.has_group(
                "access_manager_pro.group_access_manager_admin"):
            return visible
        profiles = self.env["access.manager.profile"]._applicable_profiles()
        hidden = set()
        for p in profiles:
            hidden |= set(p.hidden_menu_ids.ids)
        if hidden:
            children = self.sudo().search([("id", "child_of", list(hidden))])
            hidden |= set(children.ids)
        return visible - hidden
