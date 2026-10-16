"""Derived series from a restricted arithmetic expression (AST whitelist; no eval).

Grammar: numbers, series ids, + - * / unary -, parentheses and a few functions:
``ma(x, n)`` trailing moving average, ``diff(x, n)``, ``lag(x, n)``, ``pct(x, n)`` (percent
change), ``log1p(x)``, ``abs(x)``. Missing stays missing; division by zero gives missing.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable

import numpy as np
import pandas as pd

MAX_EXPR_LEN = 200
MAX_NODES = 60
MAX_WINDOW = 520


class ExpressionError(ValueError):
    pass


def _int_arg(node: ast.AST) -> int:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        if 1 <= node.value <= MAX_WINDOW:
            return node.value
    raise ExpressionError(f"window/lag must be an integer in 1..{MAX_WINDOW}")


def referenced_ids(expr: str) -> list[str]:
    tree = _parse(expr)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    funcs = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return sorted(names - funcs)


def _parse(expr: str) -> ast.Expression:
    if len(expr) > MAX_EXPR_LEN:
        raise ExpressionError("expression too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"syntax error: {exc.msg}") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExpressionError("expression too complex")
    return tree


def evaluate(expr: str, series: dict[str, pd.Series]) -> pd.Series:
    """Evaluate ``expr``; ``series`` maps id -> pd.Series indexed by weekly timestamps."""
    tree = _parse(expr)
    funcs: dict[str, Callable] = {
        "ma": lambda s, n: s.rolling(n, min_periods=n).mean(),
        "diff": lambda s, n: s.diff(n),
        "lag": lambda s, n: s.shift(n),
        "pct": lambda s, n: 100.0 * s.pct_change(n, fill_method=None),
        "log1p": lambda s: np.log1p(s),
        "abs": lambda s: s.abs(),
    }

    def ev(node: ast.AST):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ExpressionError("only numeric constants are allowed")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in series:
                raise ExpressionError(f"unknown series {node.id!r}")
            return series[node.id].astype(float)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub | ast.UAdd):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, ast.Add | ast.Sub | ast.Mult | ast.Div
        ):
            a, b = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(b, pd.Series):
                b = b.where(b != 0)
            elif b == 0:
                return a * math.nan
            return a / b
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name not in funcs:
                raise ExpressionError(f"unknown function {name!r}")
            if name in ("ma", "diff", "lag", "pct"):
                if len(node.args) != 2:
                    raise ExpressionError(f"{name}(x, n) takes 2 arguments")
                x = ev(node.args[0])
                if not isinstance(x, pd.Series):
                    raise ExpressionError(f"{name}() needs a series as first argument")
                return funcs[name](x, _int_arg(node.args[1]))
            if len(node.args) != 1:
                raise ExpressionError(f"{name}(x) takes 1 argument")
            x = ev(node.args[0])
            if not isinstance(x, pd.Series):
                raise ExpressionError(f"{name}() needs a series")
            return funcs[name](x)
        raise ExpressionError(f"disallowed syntax: {type(node).__name__}")

    out = ev(tree)
    if not isinstance(out, pd.Series):
        raise ExpressionError("expression must involve at least one series")
    return out.replace([np.inf, -np.inf], np.nan)
