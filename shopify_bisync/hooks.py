# -*- coding: utf-8 -*-
# Part of Shopify Connector - Two-Way Sync. License OPL-1.
"""Module lifecycle hooks (manifest: pre_init_hook / uninstall_hook /
post_load). Hook signatures per the Odoo 19 module reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
"""
import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def pre_init_check(env):
    """Fail installation with a readable message (not a traceback) when the
    ``requests`` dependency is missing from the server environment."""
    try:
        import requests  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment specific
        raise UserError(
            env._("shopify_bisync needs the Python library 'requests'. "
                  "Install it on the server (pip install requests) and retry.")
        ) from exc


def uninstall_hook(env):
    """Best-effort: delete the webhooks we registered on every store so an
    uninstalled database stops receiving (and failing) deliveries. Crons,
    server actions and models are removed by the regular uninstall; this only
    covers the state living on Shopify's side."""
    instances = env["shopify.bisync.instance"].with_context(
        active_test=False).search([])
    _logger.info("shopify_bisync uninstall: cleaning webhooks on %s store(s)",
                 len(instances))
    instances.unregister_webhooks()


def post_load():
    """Runs once at server start: warn when the pinned Shopify API version is
    within 2 quarters of its sunset date."""
    from .models.instance import api_version_sunset_warning
    api_version_sunset_warning()
