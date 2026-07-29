# -*- coding: utf-8 -*-
"""Hide whole view types from the view switcher.

When an action is loaded, the web client builds its view-switcher buttons from
the action's ``views`` / ``view_mode``.  Filtering those on read is the clean,
server-side way to remove secondary view types (kanban, pivot, graph, calendar,
activity, map) for the targeted users, without touching the action record.

Note: ``/web/action/load`` reads the action through ``sudo()``, so this
override must *not* gate on ``env.su`` - it would then never fire.  Under that
sudo read ``env.user`` is still the real user, so the configuration is fetched
in a non-su environment (``sudo(False)``) and only the app administrators /
superuser are exempted (anti-lockout).

The primary ``list`` and ``form`` views are never removed, so every action
keeps a usable landing view.
"""

from odoo import SUPERUSER_ID, models
from .access_profile import ADMIN_GROUP

# View types that may be removed; list/form are always kept.
_REMOVABLE = frozenset({"kanban", "pivot", "graph", "calendar", "activity", "map"})


class IrActionsActWindow(models.Model):
    _inherit = "ir.actions.act_window"

    def read(self, fields=None, load="_classic_read"):
        result = super().read(fields=fields, load=load)
        user = self.env.user
        if user.id == SUPERUSER_ID or user.has_group(ADMIN_GROUP):
            return result
        # Non-su config for the real user (this override runs under a sudo read).
        config = self.env["access.manager.profile"].sudo(False)._get_access_config()
        by_model = config.get("view_modes")
        if not config["restricted"] or not by_model:
            return result
        # Resolve res_model from the records themselves so stripping works even
        # when the caller did not request the ``res_model`` field.
        res_model_by_id = {rec.id: rec.res_model for rec in self}
        for action in result:
            if not isinstance(action, dict):
                continue
            hidden = by_model.get(res_model_by_id.get(action.get("id")))
            if hidden:
                self._am_strip_view_modes(action, hidden)
        return result

    @staticmethod
    def _am_strip_view_modes(action, hidden):
        hidden = _REMOVABLE & set(hidden)
        if not hidden:
            return
        if action.get("view_mode"):
            modes = [m for m in action["view_mode"].split(",") if m not in hidden]
            if modes:
                action["view_mode"] = ",".join(modes)
        if isinstance(action.get("views"), list):
            action["views"] = [v for v in action["views"]
                               if not (isinstance(v, (list, tuple)) and v[1] in hidden)]
