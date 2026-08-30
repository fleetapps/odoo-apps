# -*- coding: utf-8 -*-
"""Module lifecycle hooks. Signatures per the Odoo 19 module reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
"""
import logging

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _refuse_if_installed(env, other_module, other_name, this_name, advice):
    """Stop the install cleanly when the other edition is already present.

    The two editions define the same models and, for the connector, register
    the same routes, so a database with both gets merged field sets and a
    shadowed /mcp endpoint - which surfaces as puzzling runtime behaviour long
    after the install that caused it. Odoo has no "conflicts" manifest key, so
    a pre-init check is the only place to say so, and saying it here costs one
    readable sentence instead of a support thread.

    Remove this once the paid edition depends on the free one rather than
    duplicating it; at that point they are meant to coexist.
    """
    other = env["ir.module.module"].search([("name", "=", other_module)], limit=1)
    if other and other.state in ("installed", "to upgrade", "to install"):
        raise UserError(env._(
            "%(other)s is already installed on this database. It contains "
            "everything in %(this)s, and the two cannot run side by side - "
            "they define the same models. %(advice)s",
            other=other_name, this=this_name, advice=advice))

def pre_init_check(env):
    _refuse_if_installed(
        env, 'ai_dashboards_free', 'AI Dashboards Free', 'AI Dashboards Pro',
        'Uninstall AI Dashboards Free first; the dashboards it holds are not carried over automatically, so export any you need to keep.')
