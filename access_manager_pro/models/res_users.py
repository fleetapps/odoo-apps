# -*- coding: utf-8 -*-
"""User-level enforcement: block login for targeted users.

``_check_credentials`` is the choke point every authentication path funnels
through.  Its signature changed across versions (``password`` -> ``credential``
plus an ``env`` argument), so the override forwards ``*args``/``**kwargs``
untouched and only adds the pre-check, keeping it valid on 17, 18 and 19.
"""

from odoo import models
from odoo.exceptions import AccessDenied


class ResUsers(models.Model):
    _inherit = "res.users"

    def _check_credentials(self, *args, **kwargs):
        self._am_assert_login_allowed()
        return super()._check_credentials(*args, **kwargs)

    def _am_assert_login_allowed(self):
        if self.env.su or not self:
            return
        profile_model = self.env["access.manager.profile"].sudo()
        for user in self:
            if profile_model._login_disabled_for(user):
                raise AccessDenied()
