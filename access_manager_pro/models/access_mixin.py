# -*- coding: utf-8 -*-
"""Shared behaviour for every configuration model of Access Manager Pro.

The compiled access configuration is memoised at the registry level
(:meth:`AccessProfile._get_access_config`), and menu visibility is memoised by
Odoo itself (``ir.ui.menu.load_menus``).  Both caches must be dropped as soon
as an administrator changes a profile or one of its rule lines, otherwise
users would keep seeing a stale set of restrictions until the next restart.

Mixing this abstract model into every configuration model keeps that
invalidation in one place instead of scattered ``clear_cache`` calls.
"""

from odoo import api, models


class AccessCacheMixin(models.AbstractModel):
    _name = "access.manager.cache.mixin"
    _description = "Access Manager - Cache Invalidation Mixin"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        self.env.registry.clear_cache()
        return res

    def unlink(self):
        res = super().unlink()
        self.env.registry.clear_cache()
        return res
