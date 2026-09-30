# -*- coding: utf-8 -*-
"""Corrections to l10n_ke, and the tripwire that says when they have gone.

l10n_ke 19.0 ships four defects in the Kenyan return. None of them raises; each
produces a plausible figure that is wrong, which is why they survived into a
release and why they need something noisier than a comment.

They fall into two kinds, and the difference decides how each is fixed.

**Report expressions are static records.** box 16's apportionment formula,
box 26's carry-forward threshold and box 20's base column are real l10n_ke xml
ids that exist as soon as the module installs. They are corrected by data XML in
this module, which writes over them by external id.

**Taxes are not.** The Kenyan taxes are generated per company when the chart of
accounts loads, so they have no static external id -- only
``account.{company_id}_{template_id}``. Data XML addressed at ``ST0EXPORT``
silently does nothing. Worse, in the ODIN bake the chart loads in section 3 and
modules install in section 1, so at install time the tax does not exist at all.
That correction therefore has to be a method someone calls afterwards, and
``ke_apply_tax_corrections`` is it.

The tripwire
------------
Both kinds revert the same way. Upgrading l10n_ke reloads its own data and its
own chart template, and does not reload ours. Nothing would say so: the return
would simply start producing the old numbers again. So ``verify`` re-checks
every correction and the renderer refuses to compute a return that fails it.
"""

import logging

from odoo import _, api, models

_logger = logging.getLogger(__name__)

# Report expressions this module rewrites, by l10n_ke external id.
# data/ke_report_corrections.xml writes these values; this is what they are
# checked against afterwards. The two must be kept in step.
CORRECTED_EXPRESSIONS = {
    "l10n_ke.tax_report_non_deductible_input_vat_tax_tag": {
        "field": "formula",
        "expected": "box_15.tax - ((box_6.base / box_5.base) * box_15.tax)",
        "why": "Box 16 apportions mixed-use input VAT by the share of turnover "
               "that is taxable. The shipped formula put VAT amounts in the "
               "numerator of a turnover ratio, which for a standard-rated "
               "trader treats about 84% of the mixed input pool as "
               "non-deductible instead of none of it, and overstates the VAT "
               "payable every period.",
    },
    "l10n_ke.tax_report_net_vat_payable_or_credit_carried_forward_carryover": {
        "field": "subformula",
        "expected": "if_below(KES(0))",
        "why": "Box 26 carries a credit forward only when the period nets to a "
               "credit. The shipped threshold was denominated in Romanian leu.",
    },
}

# Report expressions this module removes outright. Checked by position on the
# report -- line code and label -- rather than by xml id, so that an
# expression put back by hand, or by a re-import with a different id, is
# caught the same way as one restored by an l10n_ke upgrade.
REMOVED_EXPRESSIONS = [
    {
        "xmlid": "l10n_ke.tax_report_total_withholding_vat_credit_base_tag",
        "report": "l10n_ke.tax_report_ke",
        "line_code": "box_20",
        "label": "base",
        "why": "Box 20 is the withholding credit our customers deducted from "
               "our sales. Its base column read a tag that only the "
               "purchase-side tax applies, and there is no sales-side base "
               "tag to point it at, so the column is left empty rather than "
               "showing the other side of the ledger. Only box 20's tax "
               "column feeds box 22, so no filed figure changes either way.",
    },
]

# Taxes whose repartition tags are wrong, as {template id: (from box, to box)}.
# The tags themselves are never named here: they are looked up through
# ke.tax.classification so that an upstream rename is a data edit.
RETAGGED_TAXES = {
    "ST0EXPORT": {
        "side": "sale",
        "label": "base",
        "wrong_box": "box_4",
        "right_box": "box_3",
        "why": "Exports are zero-rated under the VAT Act, not exempt. Tagged "
               "exempt they report in box 4 instead of box 3, and box 3 is "
               "part of the numerator of the box 16 apportionment ratio while "
               "box 4 is not, so an exporter also loses deductible input VAT.",
    },
}


class KeTaxCorrection(models.AbstractModel):
    _name = "ke.tax.correction"
    _description = "Kenyan Localisation Corrections"

    # ---------------------------------------------------------------- apply

    @api.model
    def apply(self, companies=None):
        """Re-point the Kenyan taxes whose repartition tags are wrong.

        Idempotent and safe to call at any point, including against a database
        where the chart of accounts has not loaded yet: a tax that cannot be
        resolved is reported and skipped, never created.

        :return: a list of per-company dicts the caller can log.
        """
        companies = companies or self.env["res.company"]._ke_companies()
        results = []

        for company in companies:
            outcome = {"company": company.display_name, "changes": [],
                       "skipped": []}
            for template_id, spec in RETAGGED_TAXES.items():
                outcome_line = self._retag_one(company, template_id, spec)
                key = "changes" if outcome_line[0] else "skipped"
                outcome[key].append(outcome_line[1])
            results.append(outcome)
            _logger.info(
                "ke_tax_correction: %s -> changed %s, skipped %s",
                company.display_name,
                outcome["changes"] or "nothing", outcome["skipped"] or "nothing")
        return results

    @api.model
    def _retag_one(self, company, template_id, spec):
        """:return: (changed, message)"""
        Classification = self.env["ke.tax.classification"]
        tax = company._ke_tax(template_id)
        if not tax:
            return (False, "%s: not on this chart" % template_id)

        wrong_name = Classification.tag_name_for(
            spec["wrong_box"], spec["side"], spec["label"])
        right_name = Classification.tag_name_for(
            spec["right_box"], spec["side"], spec["label"])
        if not wrong_name or not right_name:
            return (False, "%s: classification rows missing" % template_id)

        wrong_tag = company._ke_tag(wrong_name)
        right_tag = company._ke_tag(right_name)
        if not right_tag:
            return (False, "%s: tag %r not on this chart" % (template_id, right_name))

        lines = tax.repartition_line_ids.filtered(
            lambda line, t=spec["label"]: line.repartition_type == t)
        stale = lines.filtered(lambda line: wrong_tag and wrong_tag in line.tag_ids)
        if not stale:
            return (False, "%s: already correct" % template_id)

        commands = [(4, right_tag.id)]
        if wrong_tag:
            commands.insert(0, (3, wrong_tag.id))
        try:
            stale.sudo().write({"tag_ids": commands})
        except Exception:
            _logger.exception(
                "ke_tax_correction: could not retag %s on %s",
                template_id, company.display_name)
            return (False, "%s: write refused" % template_id)

        return (True, "%s: %s -> %s on %d line(s)"
                % (template_id, wrong_name, right_name, len(stale)))

    # --------------------------------------------------------------- verify

    @api.model
    def verify(self, report=None, companies=None):
        """Every correction re-checked. Returns a list of readable problems.

        Scoped: an expression is only checked against the report being rendered,
        and a tax only where the report actually distinguishes the two boxes the
        correction moves a supply between. The withholding return should not be
        held hostage to a VAT3 record.
        """
        problems = []
        problems.extend(self._verify_expressions(report))
        problems.extend(self._verify_taxes(report, companies))
        return problems

    @api.model
    def _verify_expressions(self, report):
        problems = []
        for xmlid, spec in CORRECTED_EXPRESSIONS.items():
            expression = self.env.ref(xmlid, raise_if_not_found=False)
            if not expression:
                continue
            if report and expression.report_line_id.report_id != report:
                continue
            actual = (expression[spec["field"]] or "").strip()
            if actual != spec["expected"]:
                problems.append(_(
                    "%(line)s [%(label)s]\n"
                    "   is:        %(actual)s\n"
                    "   should be: %(expected)s\n"
                    "   %(why)s",
                    line=expression.report_line_id.name, label=expression.label,
                    actual=actual or _("(empty)"), expected=spec["expected"],
                    why=spec["why"]))

        for spec in REMOVED_EXPRESSIONS:
            target_report = self.env.ref(spec["report"], raise_if_not_found=False)
            if not target_report or (report and target_report != report):
                continue
            line = target_report.line_ids.filtered(
                lambda l, code=spec["line_code"]: l.code == code)
            reinstated = line.expression_ids.filtered(
                lambda e, label=spec["label"]: e.label == label)
            if not reinstated:
                continue
            problems.append(_(
                "%(line)s [%(label)s] is present and should have been "
                "removed.\n   %(why)s",
                line=line.name, label=spec["label"], why=spec["why"]))
        return problems

    @api.model
    def _verify_taxes(self, report, companies):
        if not companies:
            return []
        Classification = self.env["ke.tax.classification"]
        problems = []

        for template_id, spec in RETAGGED_TAXES.items():
            wrong_name = Classification.tag_name_for(
                spec["wrong_box"], spec["side"], spec["label"])
            right_name = Classification.tag_name_for(
                spec["right_box"], spec["side"], spec["label"])
            if not wrong_name or not right_name:
                continue
            # Only relevant to a report that tells these two boxes apart.
            if report and not self._report_reads(report, (wrong_name, right_name)):
                continue

            for company in companies:
                tax = company._ke_tax(template_id)
                if not tax:
                    continue
                lines = tax.repartition_line_ids.filtered(
                    lambda line, t=spec["label"]: line.repartition_type == t)
                names = set(lines.tag_ids.mapped("name"))
                if wrong_name in names or right_name not in names:
                    problems.append(_(
                        "%(tax)s on %(company)s is tagged %(actual)s.\n"
                        "   It should carry %(expected)s.\n"
                        "   %(why)s\n"
                        "   Run the Kenyan tax corrections to repoint it, then "
                        "the Export Tag Repair for invoices already posted.",
                        tax=template_id, company=company.display_name,
                        actual=", ".join(sorted(names)) or _("nothing"),
                        expected=right_name, why=spec["why"]))
        return problems

    @api.model
    def _report_reads(self, report, tag_names):
        """Whether a report has any tax_tags expression on these tags."""
        formulas = {
            (expression.formula or "").strip().lstrip("-")
            for expression in report.line_ids.expression_ids
            if expression.engine == "tax_tags"
        }
        return bool(formulas & set(tag_names))
