"""The shape of a Profit & Loss: which accounts each line adds up, and the
subtotals computed from other lines.

Community Odoo 19 ships the ``account.report`` models but no engine to run
them and no P&L definition (both are Enterprise). Rather than half-implement
that engine, a layout here is deliberately small: a line either adds up the
accounts matching a domain on ``account.account`` (edited with the standard
domain widget, so account types, tags and code prefixes all work), or is a
formula over the codes of other lines.

ORM reference: https://www.odoo.com/documentation/19.0/developer/reference/backend/orm.html
"""

import ast

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain

from . import odin_pnl_formula

# The account types that make up a P&L in Odoo 19
# (addons/account/models/account_account.py, field account_type).
PL_ACCOUNT_TYPES = (
    "income",
    "income_other",
    "expense",
    "expense_other",
    "expense_depreciation",
    "expense_direct_cost",
)


class OdinPnlLayout(models.Model):
    _name = "odin.pnl.layout"
    _description = "P&L layout"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        help="Leave empty to offer this layout to every company.")
    is_default = fields.Boolean(
        string="Default",
        help="Opened when the P&L is opened without choosing a layout. One per company.")
    line_ids = fields.One2many("odin.pnl.layout.line", "layout_id", string="Lines", copy=True)

    @api.constrains("is_default", "company_id", "active")
    def _check_one_default(self):
        for layout in self.filtered(lambda layout: layout.is_default and layout.active):
            others = self.search_count([
                ("id", "!=", layout.id),
                ("is_default", "=", True),
                ("company_id", "=", layout.company_id.id),
            ])
            if others:
                raise ValidationError(_(
                    "Only one layout can be the default for %(company)s.",
                    company=layout.company_id.name or _("all companies")))

    @api.constrains("line_ids")
    def _check_lines(self):
        for layout in self:
            layout.line_ids._check_formula()
            if len(layout.line_ids.filtered("is_base")) > 1:
                raise ValidationError(_("Only one line can be the base for percentages."))
            if len(layout.line_ids.filtered("is_result")) > 1:
                raise ValidationError(_("Only one line can be the net result."))

    def _parsed_lines(self):
        """The lines in order, each with its parsed account domain or formula.

        Returns a list of dicts. Invalid lines carry an ``error`` instead of
        failing the whole report: the report says which line to fix."""
        self.ensure_one()
        parsed = []
        for line in self.line_ids.sorted(lambda line: (line.sequence, line.id)):
            entry = {"line": line, "domain": None, "formula": None, "error": False}
            if line.kind == "accounts":
                try:
                    entry["domain"] = Domain(ast.literal_eval(line.account_domain or "[]"))
                except (ValueError, SyntaxError, TypeError):
                    entry["error"] = _("The accounts of %(line)s cannot be read.", line=line.name)
            elif line.kind == "formula":
                try:
                    entry["formula"] = odin_pnl_formula.parse(line.formula)
                except odin_pnl_formula.FormulaError:
                    entry["error"] = _("The formula of %(line)s cannot be read.", line=line.name)
            parsed.append(entry)
        return parsed


class OdinPnlLayoutLine(models.Model):
    _name = "odin.pnl.layout.line"
    _description = "P&L layout line"
    _order = "sequence, id"

    layout_id = fields.Many2one("odin.pnl.layout", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Short name used in formulas, such as REV or GP. Letters, digits and underscores.")
    level = fields.Integer(default=0, help="Indentation, 0 for a top line.")
    kind = fields.Selection(
        [("heading", "Heading"), ("accounts", "Accounts"), ("formula", "Formula")],
        required=True,
        default="accounts")
    account_domain = fields.Char(
        string="Accounts",
        default="[]",
        help="Which accounts this line adds up. Only P&L accounts are ever counted.")
    formula = fields.Char(help="Arithmetic on other lines' codes, for example REV - COS.")
    sign = fields.Selection(
        [("credit", "Income (credit is positive)"), ("debit", "Expense (debit is positive)")],
        required=True,
        default="debit",
        help="How the balance reads on this line. Formula lines use the figures as displayed.")
    green_on_positive = fields.Boolean(
        string="Growth is good",
        default=True,
        help="Colour an increase green. Untick for costs, where an increase is unfavourable.")
    bold = fields.Boolean()
    hide_if_zero = fields.Boolean(help="Hide this line when all its figures are zero.")
    is_base = fields.Boolean(
        string="Base for %",
        help="The line other lines are shown as a percentage of, usually Revenue.")
    is_result = fields.Boolean(
        string="Net result",
        help="Checked against the ledger: it must equal income minus expenses of all P&L accounts.")

    _code_layout_uniq = models.Constraint(
        "UNIQUE(layout_id, code)",
        "Each line of a layout needs its own code.")

    @api.constrains("code")
    def _check_code(self):
        for line in self:
            if not line.code.isidentifier() or not line.code.isascii():
                raise ValidationError(_(
                    "The code %(code)s can only use letters, digits and underscores, "
                    "and cannot start with a digit.", code=line.code))

    @api.constrains("formula", "kind")
    def _check_formula(self):
        for layout in self.layout_id:
            known = set(layout.line_ids.mapped("code"))
            graph = {}
            for line in layout.line_ids.filtered(lambda line: line.kind == "formula"):
                try:
                    tree = odin_pnl_formula.parse(line.formula)
                except odin_pnl_formula.FormulaError as error:
                    raise ValidationError(_(
                        "The formula of %(line)s is not valid: %(error)s. Use line codes, "
                        "numbers and + - * / only.", line=line.name, error=error)) from error
                unknown = odin_pnl_formula.codes(tree) - known
                if unknown:
                    raise ValidationError(_(
                        "The formula of %(line)s uses %(codes)s, which no line has as code.",
                        line=line.name, codes=", ".join(sorted(unknown))))
                graph[line.code] = odin_pnl_formula.codes(tree)
            if _has_cycle(graph):
                raise ValidationError(_("Formulas cannot refer to each other in a loop."))


def _has_cycle(graph):
    """Whether formula references loop (A = B + 1, B = A - 1)."""
    state = {}

    def visit(code):
        if state.get(code) == "open":
            return True
        if state.get(code) == "done":
            return False
        state[code] = "open"
        if any(visit(child) for child in graph.get(code, ())):
            return True
        state[code] = "done"
        return False

    return any(visit(code) for code in graph)
