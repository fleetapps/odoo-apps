# -*- coding: utf-8 -*-
# Part of Fleet FMS Connector. License OPL-1.
"""Module lifecycle hooks. Signature per the Odoo 19 module reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
"""
from odoo.exceptions import UserError


def pre_init_check(env):
    """Fail installation with a readable message, not a traceback, when a
    hard requirement is missing: the 'requests' library (server-side
    Python, not an Odoo module), or the OCA 'purchase_request' module this
    addon extends rather than replaces."""
    try:
        import requests  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment specific
        raise UserError(
            env._("fms_connector needs the Python library 'requests'. "
                  "Install it on the server (pip install requests) and retry.")
        ) from exc
    if "purchase.request" not in env:
        raise UserError(
            env._(
                "fms_connector depends on the OCA 'purchase_request' module "
                "(OCA/purchase-workflow). Add it to the addons path and "
                "install it first."
            )
        )
