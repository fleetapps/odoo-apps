# -*- coding: utf-8 -*-
"""Apply the Kenyan tax corrections at the one moment the taxes are certain to
exist: right after the chart of accounts has been loaded.

The taxes this module corrects are generated per company by the chart of
accounts, and in Odoo 19 the chart does not load when ``l10n_ke`` installs. It
loads *after every post_init_hook of the install batch*: ``ir.module.module``
parks the load on the registry (``_auto_install_template``) and
``_register_hook`` runs it once the whole batch is in. So on the ODIN bake, or
on ``-i x_ke_vat`` into an empty database, ``hooks.post_init_apply_corrections``
resolves no ``account.{company_id}_ST0EXPORT`` and correctly does nothing --
and nothing else would run afterwards.

``account.chart.template._post_load_data`` is the hook Odoo gives every
localisation for work that needs the freshly created accounts and taxes
(l10n_be, l10n_nl, l10n_in and l10n_uk all use it). It runs on the first load,
on a reload, and on the reload an ``l10n_ke`` upgrade performs -- which is the
very event that puts the defect back. That makes the correction self-applying
on every path, and turns the renderer's integrity check into what it should
be: a tripwire that almost never fires, rather than the normal state of a
freshly baked instance.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    def _post_load_data(self, template_code, company, template_data):
        super()._post_load_data(template_code, company, template_data)
        # _get_parent_template returns the code itself plus its ancestors, so a
        # future child template of 'ke' is corrected too.
        if "ke" not in self._get_parent_template(template_code):
            return
        company = company or self.env.company
        # Never let a failed correction take the chart load down with it: the
        # renderer re-checks the taxes on every compute and will say, in as
        # many words, what is still wrong.
        try:
            results = company.ke_apply_tax_corrections()
        except Exception:
            _logger.exception(
                "x_ke_base: tax corrections could not be applied after loading "
                "the Kenyan chart on %s", company.display_name)
            return
        for result in results:
            _logger.info(
                "x_ke_base: chart loaded on %s -> corrections changed %s, "
                "skipped %s", result["company"],
                result["changes"] or "nothing", result["skipped"] or "nothing")
