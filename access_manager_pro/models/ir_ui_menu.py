# -*- coding: utf-8 -*-
"""Menu visibility.

Menus are hidden through :meth:`_load_menus_blacklist`, the extension point
that Odoo itself documents for this purpose.  It is called from within
``load_menus``, whose cache is keyed on the *user id* (not on the group set
like ``_visible_menu_ids``), so blacklisting here is correctly per-user and
does not leak between two users that happen to share the same groups.

The hook exists and behaves identically in Odoo 17, 18 and 19.
"""

from odoo import api, models
from .access_profile import SKIP_KEY


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _load_menus_blacklist(self):
        blacklist = super()._load_menus_blacklist()
        if self.env.su:
            return blacklist
        config = self.env["access.manager.profile"]._get_access_config()
        menus_cfg = config["menus"]
        if not config["restricted"] or (not menus_cfg["hidden"] and not menus_cfg["hide_apps"]):
            return blacklist

        hidden = set(menus_cfg["hidden"])
        if menus_cfg["hide_apps"]:
            hidden |= self._am_apps_menu_ids()
        if hidden:
            # Hiding a menu must hide its whole sub-tree as well.
            children = self.sudo().with_context(**{SKIP_KEY: True}).search(
                [("id", "child_of", list(hidden))])
            hidden |= set(children.ids)
        return list(set(blacklist) | hidden)

    @api.model
    def _am_apps_menu_ids(self):
        """Menu ids that lead to the Apps installer.

        Resolved from the stable ``base.open_module_tree`` action rather than a
        menu xml id, which differs between versions/editions.
        """
        action = self.env.ref("base.open_module_tree", raise_if_not_found=False)
        if not action:
            return set()
        menus = self.sudo().with_context(**{SKIP_KEY: True}).search(
            [("action", "=", "ir.actions.act_window,%d" % action.id)])
        return set(menus.ids)
