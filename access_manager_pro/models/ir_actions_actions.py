# -*- coding: utf-8 -*-
"""Contextual reports & actions.

The *Print* and *Action* menus of a form/list are populated by
``ir.actions.actions.get_bindings(model_name)``.  Filtering its result is the
clean, server-side way to remove specific reports and window actions for the
targeted users - no view surgery required.

``get_bindings`` is itself cached, so the override builds a *fresh* dict and
never mutates the cached lists it receives.
"""

from odoo import api, models


class IrActionsActions(models.Model):
    _inherit = "ir.actions.actions"

    @api.model
    def get_bindings(self, model_name):
        bindings = super().get_bindings(model_name)
        if self.env.su:
            return bindings
        config = self.env["access.manager.profile"]._get_access_config()
        if not config["restricted"]:
            return bindings
        hidden = config["reports"].get(model_name, frozenset()) \
            | config["actions"].get(model_name, frozenset())
        # "Hide the whole Print menu" drops the entire ``report`` binding type,
        # which is what makes the Print button itself disappear - the client
        # only renders it when the model has at least one bound report.
        hide_print = config["models"].get(model_name, {}) \
            .get("switches", {}).get("hide_print")
        if not hidden and not hide_print:
            return bindings
        # Note: get_bindings returns 'action' as a *tuple* (sorted) and 'report'
        # as a list on Odoo 19, so accept both sequence kinds. Rebuild fresh
        # containers - the source lists/tuples are cached and must not mutate.
        result = {}
        for key, value in bindings.items():
            if hide_print and key == "report":
                continue
            if isinstance(value, (list, tuple)):
                kept = [item for item in value if item.get("id") not in hidden]
                if kept:
                    result[key] = type(value)(kept)
            else:
                result[key] = value
        return result
