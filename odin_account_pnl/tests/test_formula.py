"""Layout formulas: arithmetic on line codes and nothing else."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from ..models import odin_pnl_formula as formula


@tagged("post_install", "-at_install")
class TestFormula(TransactionCase):

    def test_a_formula_adds_and_subtracts_lines(self):
        tree = formula.parse("REV - COS + 10")
        self.assertEqual(formula.codes(tree), {"REV", "COS"})
        self.assertEqual(formula.evaluate(tree, {"REV": 100, "COS": 40}), 70)

    def test_a_missing_figure_or_a_division_by_zero_gives_an_empty_cell(self):
        self.assertIsNone(formula.evaluate(formula.parse("REV - COS"), {"REV": 100}))
        self.assertIsNone(formula.evaluate(formula.parse("GP / REV"), {"GP": 1, "REV": 0}))

    def test_a_formula_cannot_call_code(self):
        for expression in ("__import__('os')", "REV.real", "REV[0]", "'a' + REV", "True + 1", "lambda: 1"):
            with self.subTest(expression=expression), self.assertRaises(formula.FormulaError):
                formula.parse(expression)

    def test_a_layout_refuses_unknown_codes_and_loops(self):
        layout = self.env["odin.pnl.layout"].create({"name": "Test"})
        Line = self.env["odin.pnl.layout.line"]
        Line.create({"layout_id": layout.id, "code": "A", "name": "A", "kind": "accounts"})
        with self.assertRaises(ValidationError):
            Line.create({"layout_id": layout.id, "code": "B", "name": "B", "kind": "formula", "formula": "A - X"})
        b = Line.create({"layout_id": layout.id, "code": "B", "name": "B", "kind": "formula", "formula": "A"})
        Line.create({"layout_id": layout.id, "code": "C", "name": "C", "kind": "formula", "formula": "B"})
        with self.assertRaises(ValidationError):
            b.formula = "C + 1"
