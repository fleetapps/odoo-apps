"""Formulas of P&L layout lines, such as ``REV - COS`` or ``GP / REV * 100``.

A formula is parsed with Python's own ``ast`` module and only a whitelist of
nodes is accepted: numbers, line codes, ``+ - * /`` and brackets. Nothing is
ever passed to ``eval``, so a layout edited in the UI cannot run code.
https://docs.python.org/3/library/ast.html

Values are the *displayed* figures of other lines (income positive, expenses
positive), which is how an accountant reads and writes "Gross Profit =
Revenue - Cost of Revenue". A missing value or a division by zero gives
``None``, shown as an empty cell rather than a misleading zero.
"""

import ast

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Constant,
    ast.Load,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.UAdd,
    ast.USub,
)


class FormulaError(ValueError):
    """The formula uses something other than numbers, codes and + - * /."""


def parse(expression):
    """Parse ``expression`` into a tree that :func:`evaluate` accepts.

    :raises FormulaError: on a syntax error or a forbidden construct, such as
        a function call, an attribute or a subscript.
    """
    try:
        tree = ast.parse((expression or "").strip(), mode="eval")
    except SyntaxError as error:
        raise FormulaError(str(error)) from error
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(type(node).__name__)
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, (int, float))
        ):
            raise FormulaError(repr(node.value))
    return tree


def codes(tree):
    """The line codes a parsed formula refers to."""
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def evaluate(tree, values):
    """Compute a parsed formula with ``values`` mapping each code to a number
    or ``None``. Returns ``None`` when any operand is ``None`` or on a
    division by zero."""
    return _eval(tree.body, values)


def _eval(node, values):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        value = values.get(node.id)
        return None if value is None else float(value)
    if isinstance(node, ast.UnaryOp):
        operand = _eval(node.operand, values)
        if operand is None:
            return None
        return -operand if isinstance(node.op, ast.USub) else operand
    left = _eval(node.left, values)
    right = _eval(node.right, values)
    if left is None or right is None:
        return None
    if isinstance(node.op, ast.Add):
        return left + right
    if isinstance(node.op, ast.Sub):
        return left - right
    if isinstance(node.op, ast.Mult):
        return left * right
    if right == 0:
        return None
    return left / right
