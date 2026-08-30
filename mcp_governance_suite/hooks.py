# -*- coding: utf-8 -*-
"""Module lifecycle hooks. Signatures per the Odoo 19 module reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
"""
import logging

from odoo.exceptions import UserError

from .models.mcp_scope import SUGGESTED_MODELS

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Open the default scope onto the business models this database has.

    The seeded scope in ``data/mcp_scope_data.xml`` can only reference `base`
    models: naming ``sale.order`` in a data file breaks installation on a
    database without Sales. That left a fresh install able to read four `base`
    models and nothing a business would ever ask about - connected, and useless.

    A hook can do what the data file cannot, because ``ir.model._get`` returns
    an empty recordset for an absent model, so whichever apps happen to be
    installed are picked up and the rest are skipped silently.

    Install only (Odoo calls this hook on install, never on upgrade), so an
    administrator who deliberately narrows the scope later never finds it
    widened again behind their back. Existing databases get the same thing as a
    button on the Connect screen instead.
    """
    scope = env.ref("mcp_governance_suite.scope_readonly_default",
                    raise_if_not_found=False)
    if not scope:  # pragma: no cover - the record ships in this module
        return
    added = scope.add_models(SUGGESTED_MODELS, preset="read")
    _logger.info(
        "MCP: opened '%s' onto %s of %s suggested business model(s); the rest "
        "are not installed in this database.",
        scope.name, len(added), len(SUGGESTED_MODELS))


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
        env, 'ai_mcp', 'AI MCP', 'AI MCP Pro',
        'Uninstall AI MCP first; AI MCP Pro is a superset of it and your scopes, connections and audit history are not carried over automatically, so export anything you need to keep.')
