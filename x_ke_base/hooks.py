# -*- coding: utf-8 -*-
"""Apply the Kenyan tax corrections on install, where that is possible.

On an ordinary install into a database that already has the Kenyan chart of
accounts, this fixes the taxes immediately and nobody has to know the repair
exists.

When the chart is not there yet -- the ODIN bake installs every module in one
batch and Odoo 19 loads the chart only after the batch, and ``-i x_ke_vat``
into an empty database behaves the same way -- there is no
``account.{company_id}_ST0EXPORT`` to re-point. This hook then resolves
nothing, reports it, and returns. The correction is applied instead by
``models/account_chart_template.py``, which hooks ``_post_load_data`` and
runs the same method the moment the Kenyan chart lands.

Both paths run the same idempotent method, so it does not matter if both fire.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_apply_corrections(env):
    """Idempotent, and never a reason for an install to fail."""
    try:
        results = env["res.company"].ke_apply_tax_corrections()
    except Exception:
        _logger.exception(
            "x_ke_base: tax corrections could not be applied on install. "
            "Run them from Kenya Tax > Configuration > Repair Tax Tags "
            "(Apply Tax Corrections), or call "
            "env['res.company'].ke_apply_tax_corrections() once the chart of "
            "accounts is in place.")
        return

    changed = sum(len(r["changes"]) for r in results)
    if changed:
        _logger.info("x_ke_base: %d tax correction(s) applied on install", changed)
    else:
        _logger.info(
            "x_ke_base: no tax corrections applied yet. This is expected when "
            "the chart of accounts has not loaded; they are applied "
            "automatically when it does (account.chart.template._post_load_data).")
