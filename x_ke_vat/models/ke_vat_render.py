# -*- coding: utf-8 -*-
"""The renderer: three computation engines, and nothing else.

Odoo Community ships the ``account.report`` framework -- the report, line,
expression, column and external-value models, the formula validation, the
dependency expansion, the tag resolution -- and ships Kenya's VAT3 and
withholding-VAT return definitions written against it. What it does not ship is
the code that evaluates a formula against the ledger. That is ``account_reports``,
which is Enterprise.

This file is that evaluation layer, deliberately scoped to what Kenya uses.
Counting every expression across ``l10n_ke``'s two report definitions gives
twenty ``tax_tags``, fifteen ``aggregation`` and seven ``external``. It gives
zero ``account_codes``, zero ``domain`` and zero ``custom``, so those three are
not implemented and say so when encountered, rather than silently returning a
plausible zero.

Why an AbstractModel rather than an ``_inherit`` of ``account.report``
---------------------------------------------------------------------
Two reasons, one of them load-bearing. It matches the house pattern --
``ai.dashboard.render`` and ``ai.dashboard.pivot`` are both AbstractModels for
the same reason, that non-persistent logic stays overridable and reachable
through the ORM. And it keeps a zero-collision surface: if Enterprise
``account_reports`` is ever installed into the same database, a method named
``_compute_formula_batch_with_engine_tax_tags`` on ``account.report`` would
shadow or be shadowed by Odoo's own, with the winner decided by module load
order. None of the methods here use those names, and none of them should.

What this renderer is not
-------------------------
It has no comparison columns, no growth columns, no foldable hierarchy, no
analytic filters, no prefix groups, no currency translation, no horizontal
splits and no report sections. Enterprise's report UI has all of those, and
reimplementing them is where a project like this quietly doubles in size. None
of them is needed to compute a VAT3 and reconcile it.
"""

import logging
import math
import re
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tools.safe_eval import safe_eval
_logger = logging.getLogger(__name__)

# A reference to another line's expression, e.g. "box_1.base" or
# "box_19._applied_carryover_tax". Labels may start with an underscore.
AGGREGATION_TERM_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*")

# Aggregation subformula: keep the value only while it is below a threshold
# expressed in some currency, e.g. "if_below(KES(0))".
IF_BELOW_RE = re.compile(r"^if_below\(\s*([A-Za-z]{3})\(\s*(-?[0-9.]+)\s*\)\s*\)$")

# External subformula: "editable;rounding=2".
EXTERNAL_ROUNDING_RE = re.compile(r"rounding\s*=\s*(\d+)")

SUPPORTED_ENGINES = ("tax_tags", "aggregation", "external")
SUPPORTED_DATE_SCOPES = ("strict_range", "previous_return_period")

# The corrections themselves, and the checks that say when they have been
# reverted, live in x_ke_base.ke.tax.correction. They are stated once there
# because the eTIMS transmitter has to refuse the same reverted data that this
# renderer refuses, and a second copy here would be a second thing to forget.


class KeVatRender(models.AbstractModel):
    _name = "ke.vat.render"
    _description = "Kenya VAT Report Renderer"

    # ----------------------------------------------------------- public API

    @api.model
    def render(self, report, options):
        """Evaluate one ``account.report`` and return its lines.

        :param report: an ``account.report`` record or id.
        :param options: see ``_normalise_options`` -- a small, flat dict. This
            is deliberately not Enterprise's options engine.
        :return: ``{'columns': [...], 'lines': [...], 'expression_values': {},
            'options': {...}}``. ``expression_values`` is keyed by expression id
            and includes the internal ``_carryover_*`` labels that no column
            renders, because closing a period needs them.
        """
        if isinstance(report, int):
            report = self.env["account.report"].browse(report)
        report.ensure_one()
        options = self._normalise_options(options)

        self._check_definition_integrity(report, options)

        expressions = report.line_ids.expression_ids
        expressions |= expressions._expand_aggregations()
        self._check_supported(expressions)

        # (line code, expression label) -> expression. Lines without a code --
        # the two unnamed section headers in the withholding report -- cannot
        # be referenced by a formula and simply never appear here.
        by_key = {
            (expr.report_line_id.code, expr.label): expr
            for expr in expressions
            if expr.report_line_id.code
        }

        values = {}
        self._eval_tax_tags(
            report, expressions.filtered(lambda e: e.engine == "tax_tags"),
            options, values)
        self._eval_external(
            expressions.filtered(lambda e: e.engine == "external"),
            options, values)
        self._eval_aggregations(
            expressions.filtered(lambda e: e.engine == "aggregation"),
            by_key, options, values)

        return {
            "columns": [
                {
                    "name": column.name,
                    "expression_label": column.expression_label,
                    "figure_type": column.figure_type,
                    "blank_if_zero": column.blank_if_zero,
                }
                for column in report.column_ids
            ],
            "lines": self._build_lines(report, values),
            "expression_values": values,
            "options": options,
        }

    @api.model
    def _normalise_options(self, options):
        """Validate and complete the options dict.

        The whole vocabulary is five keys::

            date_from, date_to   the return period (a calendar month in Kenya)
            company_ids          companies to include
            state                'posted' or 'all'
            journal_ids          [] means every journal
        """
        options = dict(options or {})
        for key in ("date_from", "date_to"):
            if not options.get(key):
                raise UserError(_("The VAT report needs a %s date.", key))
        options["date_from"] = self._to_date(options["date_from"])
        options["date_to"] = self._to_date(options["date_to"])
        if options["date_from"] > options["date_to"]:
            raise UserError(_("The period starts after it ends."))

        options.setdefault("company_ids", self.env.companies.ids)
        if not options["company_ids"]:
            raise UserError(_("The VAT report needs at least one company."))
        options.setdefault("state", "posted")
        if options["state"] not in ("posted", "all"):
            raise UserError(_(
                "Unknown entry state %(state)s: expected 'posted' or 'all'.",
                state=options["state"]))
        options.setdefault("journal_ids", [])
        return options

    @api.model
    def _to_date(self, value):
        """Accept a date, a datetime or an ISO string, return a date."""
        return fields.Date.to_date(value)

    # ------------------------------------------------------------ integrity

    @api.model
    def _check_definition_integrity(self, report, options=None):
        """Refuse to render a definition whose corrections have been reverted.

        Every defect this module corrects produces a plausible number rather
        than an error, and all of them revert the same silent way: upgrading
        l10n_ke reloads its own data and its own chart template and does not
        reload ours. So the corrections are re-checked on every compute, and a
        reverted one stops the return instead of skewing it.

        The checking itself belongs to x_ke_base, which owns the corrections.
        """
        companies = self.env["res.company"].browse(
            (options or {}).get("company_ids") or [])
        problems = self.env["ke.tax.correction"].verify(report, companies)
        if problems:
            raise UserError(_(
                "The Kenyan tax setup has been reverted to a state this module "
                "corrects, so the figures it would produce are wrong. This "
                "usually means l10n_ke was upgraded after x_ke_base; upgrading "
                "x_ke_base re-applies the report corrections, and running the "
                "Kenyan tax corrections re-applies the tax ones.\n\n"
                "%(problems)s", problems="\n\n".join(problems)))

    @api.model
    def _check_supported(self, expressions):
        bad = expressions.filtered(lambda e: e.engine not in SUPPORTED_ENGINES)
        if bad:
            raise UserError(_(
                "This renderer implements the %(supported)s engines, which are "
                "the only ones Kenya's returns use. These expressions need "
                "something else:\n%(bad)s",
                supported=", ".join(SUPPORTED_ENGINES),
                bad="\n".join(
                    f"  {e.display_name}: {e.engine}" for e in bad)))

        bad_scope = expressions.filtered(
            lambda e: e.date_scope not in SUPPORTED_DATE_SCOPES)
        if bad_scope:
            raise UserError(_(
                "This renderer implements the %(supported)s date scopes, which "
                "are the only ones Kenya's returns use. These expressions need "
                "something else:\n%(bad)s",
                supported=", ".join(SUPPORTED_DATE_SCOPES),
                bad="\n".join(
                    f"  {e.display_name}: {e.date_scope}" for e in bad_scope)))

    # ------------------------------------------------------------- the dates

    @api.model
    def _scope_dates(self, date_scope, options):
        """Resolve an expression's date scope to a concrete range.

        Community declares six scopes and interprets none of them -- there is
        no helper anywhere in ``account`` that turns one into a date range.
        Kenya uses two, so two are implemented.
        """
        if date_scope == "strict_range":
            return options["date_from"], options["date_to"]
        if date_scope == "previous_return_period":
            # Resolved off the statutory periods the deployment seeded, so that
            # "the previous return period" means the same dates here as in
            # every other report filter on the instance. Where none are seeded
            # it falls back to calendar months, which for Kenya is the same
            # answer. The withholding return's "paid before the 20th of the
            # selected month" section is what reads this.
            company = self.env["res.company"].browse(options["company_ids"][0])
            return company._ke_previous_period(
                options["date_from"], options["date_to"])
        raise UserError(_("Unsupported date scope %(scope)s.", scope=date_scope))

    @api.model
    def _aml_domain(self, report, options, date_from, date_to):
        """The journal-item domain behind every tax-tag figure and every drill."""
        domain = Domain([
            ("company_id", "in", options["company_ids"]),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
        ])
        if options["state"] == "posted":
            domain &= Domain("parent_state", "=", "posted")
        else:
            domain &= Domain("parent_state", "in", ("posted", "draft"))
        if options["journal_ids"]:
            domain &= Domain("journal_id", "in", options["journal_ids"])
        if report.only_tax_exigible:
            # tax_report_ke inherits only_tax_exigible=True from its root report,
            # account.generic_tax_report -- see _compute_report_option_filter.
            # Cash-basis taxes must stay out of the return until they are paid.
            domain &= self.env["account.move.line"]._get_tax_exigible_domain()
        return domain

    # -------------------------------------------------------------- tax_tags

    @api.model
    def _eval_tax_tags(self, report, expressions, options, values):
        """One grouped read per date scope, not one query per expression."""
        if not expressions:
            return

        by_scope = defaultdict(lambda: self.env["account.report.expression"])
        for expr in expressions:
            by_scope[expr.date_scope] |= expr

        for date_scope, scope_exprs in by_scope.items():
            date_from, date_to = self._scope_dates(date_scope, options)
            tags = scope_exprs._get_matching_tags()
            if not tags:
                for expr in scope_exprs:
                    values[expr.id] = 0.0
                continue

            domain = self._aml_domain(report, options, date_from, date_to)
            domain &= Domain("tax_tag_ids", "in", tags.ids)
            rows = self.env["account.move.line"]._read_group(
                domain, groupby=["tax_tag_ids"], aggregates=["balance:sum"])
            balance_by_tag = {tag.id: balance for tag, balance in rows}

            # _get_matching_tags reads with lang='en_US', and tags are unique
            # per (name, applicability, country), so a name maps to one tag.
            tags_by_name = defaultdict(list)
            for tag in tags:
                tags_by_name[tag.name].append(tag.id)

            for expr in scope_exprs:
                formula = (expr.formula or "").strip()
                total = sum(
                    balance_by_tag.get(tag_id, 0.0)
                    for tag_id in tags_by_name.get(formula.lstrip("-"), ())
                )
                # The sign lives on this expression's own formula, not on the
                # tag. account.account.tag.balance_negate would give the same
                # answer, but it is a non-stored field whose SQL joins tag name
                # to expression formula with no country filter, so it can match
                # another localisation's report. Reading our own formula cannot.
                values[expr.id] = -total if formula.startswith("-") else total

    # -------------------------------------------------------------- external

    @api.model
    def _eval_external(self, expressions, options, values):
        """User-entered values and the credit carried forward.

        Community ships the ``account.report.external.value`` model in full,
        carryover fields included, with access rights and a company record rule
        -- and nothing at all that reads or writes it. Both halves are ours.
        """
        external_value = self.env["account.report.external.value"]
        for expr in expressions:
            formula = (expr.formula or "").strip()
            date_from, date_to = self._scope_dates(expr.date_scope, options)
            base = Domain([
                ("target_report_expression_id", "=", expr.id),
                ("company_id", "in", options["company_ids"]),
            ])

            if formula == "sum":
                records = external_value.search(
                    base & Domain([("date", ">=", date_from),
                                   ("date", "<=", date_to)]))
                result = sum(records.mapped("value"))
            elif formula == "most_recent":
                record = external_value.search(
                    base & Domain("date", "<=", date_to),
                    order="date desc, id desc", limit=1)
                result = record.value if record else 0.0
            else:
                raise UserError(_(
                    "%(expression)s uses the external engine with formula "
                    "%(formula)s. Only 'sum' and 'most_recent' are implemented.",
                    expression=expr.display_name, formula=formula))

            rounding = self._external_rounding(expr)
            values[expr.id] = round(result, rounding) if rounding is not None else result

    @api.model
    def _external_rounding(self, expression):
        """Decimal places from an ``editable;rounding=2`` subformula."""
        match = EXTERNAL_ROUNDING_RE.search(expression.subformula or "")
        return int(match.group(1)) if match else None

    @api.model
    def is_editable(self, expression):
        """Whether the UI should let someone type into this box."""
        return (expression.engine == "external"
                and "editable" in (expression.subformula or ""))

    # ----------------------------------------------------------- aggregation

    @api.model
    def _eval_aggregations(self, expressions, by_key, options, values):
        for expr in self._sort_by_dependency(expressions, by_key):
            values[expr.id] = self._eval_one_aggregation(
                expr, by_key, options, values)

    @api.model
    def _aggregation_dependencies(self, expression, by_key):
        """The expressions one aggregation formula reads.

        ``sum_children`` is handled separately and deliberately never reaches
        ``_get_aggregation_terms_details``: that helper splits each term on a
        dot, and 'sum_children' has none, so it would raise a bare ValueError.
        Community's own ``_expand_aggregations`` special-cases it for the same
        reason.
        """
        if (expression.formula or "").strip() == "sum_children":
            label = expression.label
            children = expression.report_line_id.children_ids
            return children.expression_ids.filtered(lambda e: e.label == label)

        found = self.env["account.report.expression"]
        for code, labels in expression._get_aggregation_terms_details().items():
            for label in labels:
                dependency = by_key.get((code, label))
                if dependency:
                    found |= dependency
        return found

    @api.model
    def _sort_by_dependency(self, expressions, by_key):
        """Order aggregations so every formula runs after what it reads."""
        pending = list(expressions)
        aggregation_ids = {expr.id for expr in pending}
        blockers = {
            expr.id: {
                dep.id for dep in self._aggregation_dependencies(expr, by_key)
            } & aggregation_ids
            for expr in pending
        }

        ordered, done = [], set()
        while len(ordered) < len(pending):
            progressed = False
            for expr in pending:
                if expr.id in done or blockers[expr.id] - done:
                    continue
                ordered.append(expr)
                done.add(expr.id)
                progressed = True
            if not progressed:
                stuck = [e.display_name for e in pending if e.id not in done]
                raise UserError(_(
                    "These report formulas depend on each other in a circle, "
                    "so none of them can be computed:\n%(stuck)s",
                    stuck="\n".join(f"  {name}" for name in stuck)))
        return ordered

    @api.model
    def _eval_one_aggregation(self, expression, by_key, options, values):
        formula = (expression.formula or "").strip()
        subformula = (expression.subformula or "").strip()

        if formula == "sum_children":
            result = sum(
                values.get(child.id, 0.0)
                for child in self._aggregation_dependencies(expression, by_key)
            )
        else:
            # Each referenced term becomes a name bound to its value, rather
            # than its repr() pasted into the string. Community has already
            # validated the grammar, so this is only about not building a
            # program out of text: a value of inf or nan has no literal form
            # and would turn into a NameError halfway through a VAT return.
            namespace = {}

            def substitute(match):
                term = match.group(0)
                code, _sep, label = term.partition(".")
                dependency = by_key.get((code, label))
                name = "t_" + re.sub(r"\W", "_", term)
                namespace[name] = float(
                    values.get(dependency.id, 0.0) if dependency else 0.0)
                return name

            arithmetic = AGGREGATION_TERM_RE.sub(substitute, formula)
            try:
                # Odoo 19 signature: safe_eval(expr, /, context=None, *, ...).
                result = safe_eval(arithmetic, namespace)
            except ZeroDivisionError:
                if subformula != "ignore_zero_division":
                    raise UserError(_(
                        "%(expression)s divided by zero and is not marked "
                        "ignore_zero_division.",
                        expression=expression.display_name))
                result = 0.0

        result = self._apply_subformula(expression, subformula, result, options)

        result = float(result)
        if not math.isfinite(result):
            raise UserError(_(
                "%(expression)s computed to %(result)s, which is not a number "
                "a return can carry. Check the figures the formula reads.",
                expression=expression.display_name, result=result))
        return result

    @api.model
    def _apply_subformula(self, expression, subformula, result, options):
        """Apply the subformula, or refuse one we do not implement.

        Whitelisted deliberately. An unrecognised subformula silently ignored
        is the same class of failure as the defects this module corrects: the
        box still shows a number, and the number is wrong.
        """
        if not subformula or subformula == "ignore_zero_division":
            # ignore_zero_division is handled where the division happens.
            return result
        if subformula.startswith("if_below("):
            return self._apply_if_below(expression, subformula, result, options)
        raise UserError(_(
            "%(expression)s uses the subformula %(subformula)s. This renderer "
            "implements ignore_zero_division and if_below(CUR(amount)), which "
            "are the ones Kenya's returns use.",
            expression=expression.display_name, subformula=subformula))

    @api.model
    def _apply_if_below(self, expression, subformula, result, options):
        """Keep the value only while it is below a threshold, else zero.

        Box 26 uses this to carry a credit forward without carrying a liability
        forward. The threshold is written in a currency, and l10n_ke shipped it
        in Romanian leu -- this module corrects that to KES, but the conversion
        is still guarded: an inactive currency with no rate must not be the
        reason a VAT return cannot be computed.
        """
        match = IF_BELOW_RE.match(subformula)
        if not match:
            raise UserError(_(
                "%(expression)s has an if_below subformula this renderer "
                "cannot read: %(subformula)s",
                expression=expression.display_name, subformula=subformula))

        currency_name, raw_threshold = match.group(1), float(match.group(2))
        company = self.env["res.company"].browse(options["company_ids"][0])
        threshold = raw_threshold

        currency = self.env["res.currency"].with_context(
            active_test=False).search([("name", "=", currency_name)], limit=1)
        if not currency:
            _logger.warning(
                "x_ke_vat: %s names an unknown currency %s; comparing "
                "against %s directly",
                expression.display_name, currency_name, raw_threshold)
        elif currency != company.currency_id and raw_threshold:
            try:
                threshold = currency._convert(
                    raw_threshold, company.currency_id, company,
                    options["date_to"])
            except Exception:
                _logger.warning(
                    "x_ke_vat: could not convert %s %s to %s for %s; "
                    "comparing against the unconverted amount",
                    raw_threshold, currency_name, company.currency_id.name,
                    expression.display_name)

        return result if result < threshold else 0.0

    # ----------------------------------------------------------- the output

    @api.model
    def _build_lines(self, report, values):
        """Flatten the report into rows, in the order it is meant to be read.

        No recursion is needed. ``account.report.line._order`` is
        ``sequence, id`` and Community's ``_validate_parent_sequence``
        constraint guarantees a parent always sorts before its children, so the
        flat order is already a valid pre-order walk of the tree.
        ``hierarchy_level`` then gives the indent depth.
        """
        lines = []
        for line in report.line_ids:
            row_values = {}
            for column in report.column_ids:
                expression = line.expression_ids.filtered(
                    lambda e, label=column.expression_label: e.label == label)
                row_values[column.expression_label] = (
                    values.get(expression.id, 0.0) if expression else None)
            lines.append({
                "id": line.id,
                "code": line.code or "",
                "name": line.name,
                "level": line.hierarchy_level,
                "sequence": line.sequence,
                "expression_ids": line.expression_ids.ids,
                "values": row_values,
            })
        return lines
