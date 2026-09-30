# -*- coding: utf-8 -*-
"""User-level enforcement: login blocking and external-API (RPC) blocking.

``_check_credentials`` is the choke point every authentication path funnels
through.  Its signature changed across versions (``password`` -> ``credential``
plus an ``env`` argument), so the overrides forward ``*args``/``**kwargs``
untouched and only add the pre-checks, keeping them valid on 17, 18 and 19.

Blocking the external API
-------------------------
Odoo itself already tells us whether a credential is being presented by a human
in a browser or by a script: ``_check_credentials(credential, env)`` receives
``env['interactive']``, which is ``False`` on every non-interactive path
(``odoo/service/model.py`` -> ``res.users._check_uid_passwd`` for
``execute_kw``, and ``odoo/service/common.py:exp_authenticate`` for
``common.login`` / ``common.authenticate``).  Refusing there covers XML-RPC and
JSON-RPC in one place, without guessing at URLs or user agents.

The modern ``/json/2`` endpoints authenticate with an API key instead
(``ir.http._auth_method_bearer`` -> ``res.users.apikeys._check_credentials``),
so that method is guarded as well - which also disables any key the user may
already have generated.

References
----------
* External API .. https://www.odoo.com/documentation/19.0/developer/reference/external_api.html
"""

from odoo import api, models
from odoo.exceptions import AccessDenied

from .access_profile import SKIP_KEY


class ResUsers(models.Model):
    _inherit = "res.users"

    # ------------------------------------------------------------------ #
    #  Default profiles for new users
    # ------------------------------------------------------------------ #
    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._am_apply_default_profiles()
        return users

    def _am_apply_default_profiles(self):
        """Enrol brand-new users into the profiles that ask for them.

        Doing it on create (rather than by resolving 'new users' at read time)
        keeps the profile a plain list of users: an administrator can see who a
        profile actually covers, and can remove somebody from it again without
        the rule silently putting them back.
        """
        profile_model = self.env["access.manager.profile"]
        by_profile = {}
        for user in self:
            for profile in profile_model._profiles_for_new_user(user):
                by_profile.setdefault(profile, []).append(user.id)
        for profile, user_ids in by_profile.items():
            profile.sudo().with_context(**{SKIP_KEY: True}).write(
                {"user_ids": [(4, uid) for uid in user_ids]})

    # ------------------------------------------------------------------ #
    #  Authentication barriers
    # ------------------------------------------------------------------ #
    def _check_credentials(self, *args, **kwargs):
        self._am_assert_login_allowed()
        if not self._am_is_interactive(args, kwargs):
            self._am_assert_rpc_allowed()
        return super()._check_credentials(*args, **kwargs)

    @staticmethod
    def _am_is_interactive(args, kwargs):
        """Read ``env['interactive']`` out of a ``_check_credentials`` call.

        ``env`` is the second positional argument on 17-19. When it is missing
        or malformed we assume an interactive login, exactly like Odoo does -
        an unknown caller must not be treated as a script and locked out.
        """
        env = kwargs.get("env")
        if env is None and len(args) > 1:
            env = args[1]
        if not isinstance(env, dict):
            return True
        return env.get("interactive", True)

    # Neither guard may short-circuit on ``env.su``. ``res.users._login`` calls
    # ``_check_credentials`` on ``user.with_user(user).sudo()``, so the normal
    # web login - and ``common.authenticate`` with it - always arrives here in
    # superuser mode; an ``env.su`` early return silently disables the whole
    # check on the one path that matters most. Anti-lockout is enforced inside
    # ``_am_switch_active_for`` instead (superuser, this app's administrators
    # and settings administrators are exempt there).
    def _am_assert_login_allowed(self):
        if not self:
            return
        profile_model = self.env["access.manager.profile"].sudo()
        for user in self:
            if profile_model._login_disabled_for(user):
                raise AccessDenied()

    def _am_assert_rpc_allowed(self):
        if not self:
            return
        profile_model = self.env["access.manager.profile"].sudo()
        for user in self:
            if profile_model._rpc_disabled_for(user):
                raise AccessDenied()


class ResUsersApikeys(models.Model):
    _inherit = "res.users.apikeys"

    def _check_credentials(self, *, scope, key):
        """Refuse a valid API key that belongs to an API-blocked user.

        Returning a falsy uid is the documented "unknown key" answer: the
        bearer route answers ``401 Invalid apikey`` and
        ``res.users._check_credentials`` falls through to ``AccessDenied``. It
        deliberately looks identical to a wrong key, so a blocked key cannot be
        distinguished from a revoked one.
        """
        uid = super()._check_credentials(scope=scope, key=key)
        if not uid:
            return uid
        user = self.env["res.users"].sudo().browse(uid)
        if self.env["access.manager.profile"].sudo()._rpc_disabled_for(user):
            return False
        return uid
