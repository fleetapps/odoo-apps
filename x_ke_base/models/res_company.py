# -*- coding: utf-8 -*-
"""Company-scoped lookups every Kenyan module needs, and nothing else.

Three kinds of thing live here, and they are together because all three are
facts about one company that both the VAT return and the eTIMS transmitter have
to agree on.

**Taxes and tags cannot be addressed statically.** The Kenyan chart generates
them per company, so a tax has the external id ``account.{company_id}_ST16`` and
a tag exists once per country with a name but no useful xml id. Every consumer
resolves them the same way, here, so none of them can drift.

**Statutory periods belong to the deployment, not to us.** The ODIN bake seeds
``date.range`` records of type "Kenya VAT Period" -- one per month, named with
the filing deadline. Inventing a parallel period model alongside them would give
two people reconciling the same return two different sets of dates. So we read
theirs.

That read is deliberately soft. ``date_range`` is AGPL-3 and this module is
OPL-1, so it cannot appear in ``depends``; and a template built without it must
still work. The pattern is the one the bake itself uses for the same module:
check the model is there, fall back to calendar arithmetic when it is not.
Kenyan VAT periods are calendar months either way, so the fallback is exact
rather than approximate -- what is lost without ``date.range`` is the shared
vocabulary, not correctness.
"""

import logging

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# The date.range.type the ODIN bake creates for Kenya. Matched by name because
# the bake creates it by name and neither side owns an xml id for it.
KE_PERIOD_TYPE = "Kenya VAT Period"


class ResCompany(models.Model):
    _inherit = "res.company"

    # ------------------------------------------------------ Kenyan lookups

    @api.model
    def _ke_companies(self):
        """Every company running the Kenyan chart of accounts."""
        kenya = self.env.ref("base.ke", raise_if_not_found=False)
        if not kenya:
            return self.env["res.company"]
        return self.env["res.company"].sudo().search(
            [("account_fiscal_country_id", "=", kenya.id)])

    def _ke_tax(self, template_id):
        """A Kenyan tax by its l10n_ke template id, e.g. 'ST16', 'ST0EXPORT'.

        Empty before the chart of accounts has loaded, which is the normal
        state during the bake's module-install section. Callers must cope.
        """
        self.ensure_one()
        xmlid = self.env["account.chart.template"].company_xmlid(
            template_id, self)
        return self.env.ref(xmlid, raise_if_not_found=False) or self.env["account.tax"]

    def _ke_tag(self, tag_name):
        """A Kenyan tax tag by name, or an empty recordset.

        Tags are unique per (name, applicability, country), so this returns at
        most one. Odoo 19 carries the report line's sign on the expression
        formula rather than on the tag, so there is no signed pair to
        disambiguate as there was before 19.
        """
        self.ensure_one()
        country = self.env.ref("base.ke", raise_if_not_found=False)
        if not country or not tag_name:
            return self.env["account.account.tag"]
        return self.env["account.account.tag"]._get_tax_tags(tag_name, country.id)

    # --------------------------------------------------------- corrections

    def ke_apply_tax_corrections(self):
        """Apply the Kenyan tax corrections to these companies.

        Public, idempotent, and the entry point the deployment calls after the
        chart of accounts has loaded. Safe to call before that too: a tax that
        does not resolve is reported and skipped.

        Called with no companies it does every Kenyan one, so the bake can
        simply say ``env.company.ke_apply_tax_corrections()`` or
        ``env['res.company'].ke_apply_tax_corrections()`` and get the same
        result.

        :return: a list of per-company dicts, suitable for logging.
        """
        # Public, so reachable over RPC and from the repair wizard's button.
        # It only ever repoints a tax to the tag its box already reads, but it
        # writes tax configuration with sudo, so it is gated the way the
        # wizard is. Superuser covers the post_init hook, the chart-template
        # post-load hook and the bake's ``odoo shell`` session.
        if not (self.env.is_system()
                or self.env.user.has_group("x_ke_base.group_ke_tax_manager")):
            raise AccessError(_(
                "Only a Kenya Tax manager or an administrator can apply the "
                "Kenyan tax corrections."))
        companies = self or self._ke_companies()
        return self.env["ke.tax.correction"].apply(companies)

    # ------------------------------------------------------------- periods

    def _ke_period_ranges(self):
        """The statutory periods the deployment seeded, newest last.

        An empty list means ``date_range`` is not installed, or nobody has
        seeded it for this company. Both are normal.
        """
        self.ensure_one()
        if "date.range" not in self.env:
            return []
        return self.env["date.range"].search(
            [("type_id.name", "=", KE_PERIOD_TYPE),
             ("company_id", "in", [self.id, False])],
            order="date_start")

    def _ke_period_name(self, date_from, date_to):
        """What the deployment calls this period, e.g. '2026-08 (due 20 Sep 2026)'.

        Falls back to the plain month when there is no matching range, so the
        label is always something a person recognises.
        """
        self.ensure_one()
        for period in self._ke_period_ranges() or []:
            if period.date_start == date_from and period.date_end == date_to:
                return period.name
        return fields.Date.to_date(date_from).strftime("%Y-%m")

    def _ke_previous_period(self, date_from, date_to):
        """The period before this one, as (date_from, date_to).

        Resolved off the seeded ranges where they exist, so that "the previous
        return period" means the same thing here as it does in every other
        report filter on the instance. Kenyan VAT periods are calendar months,
        so the arithmetic fallback lands on the same dates.

        Only a range that ends the day before this period starts counts as
        "previous". The bake seeds a bounded window of months and an instance
        is never re-seeded, so the newest seeded range eventually lies months
        behind the period being filed. Taking "the latest range before
        date_from" would then quietly hand back that stale month, and box 19
        would read its credit from the wrong period. Adjacent or nothing.
        """
        self.ensure_one()
        previous_end = date_from - relativedelta(days=1)
        for period in self._ke_period_ranges() or []:
            if period.date_end == previous_end:
                return period.date_start, period.date_end
        _logger.debug(
            "x_ke_base: no seeded period ending %s; using calendar months",
            previous_end)
        return date_from - relativedelta(months=1), previous_end
