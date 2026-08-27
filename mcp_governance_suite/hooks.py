# -*- coding: utf-8 -*-
"""Module lifecycle hooks. Signatures per the Odoo 19 module reference:
https://www.odoo.com/documentation/19.0/developer/reference/backend/module.html
"""
import logging

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
