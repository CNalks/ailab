"""Derived (composite) series: safe expressions, kind inference, alignment.

Parsing/evaluation is delegated to the maintained ``simpleeval`` library, hardened twice:

1. a whitelist walk over the Python AST accepts only numbers, series names, ``+ - * /``, unary
   ``+ -``, parentheses and calls of whitelisted functions with positional arguments (no
   attributes, subscripts, comprehensions, lambdas, strings, ``**``, comparisons, keywords);
2. the ``simpleeval`` evaluator itself is stripped of every node type except those, and series
   are wrapped in :class:`TS`, an object with no useful attributes, so there is nothing to reach.

Nothing is ever passed to ``eval``/``exec``/``compile``.

Semantics (documented in docs/DERIVED_SERIES.md):

* **Missing is never zero.** NaN propagates through every operator; ``a / b`` with ``b == 0`` is
  undefined (NaN); rolling windows need all ``n`` weeks present; ``sum()`` is NaN if any term is.
* **Kinds are inferred** by operator rules (count+count -> count, count/count -> proportion,
  ...); a proportion that leaves ``[0, scale]`` is downgraded to ``rate``. Aggregating proportions
  with a plain mean/sum is rejected; use ratio of sums (``rolling_ratio``, ``sum(a,b)/sum(c,d)``).
* **Alignment**: all sources must be weekly on the same weekday grid; the result range is the
  intersection (default) or union of the sources' ranges.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import simpleeval

MAX_EXPR_LEN = 300
MAX_NODES = 80
MAX_WINDOW = 520
MAX_SOURCES = 8
MAX_CONST = 1e12
ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


class ExpressionError(ValueError):
    """Invalid, unsafe or semantically impossible expression (HTTP 422)."""


class AlignmentError(ValueError):
    """Sources cannot be aligned (frequency, weekday phase, no overlap)."""


# --------------------------------------------------------------------------------------
# typed series
# --------------------------------------------------------------------------------------
@dataclass
class TS:
    """A weekly series plus its kind. Operators implement the kind-inference rules."""

    s: pd.Series
    kind: str
    scale: int = 1  # proportion upper bound (1 or 100)
    notes: list[str] = field(default_factory=list)
    mult: float = 1.0  # pure scalar factor applied to a count (so `100 * A / B` is a percent)

    # operators ---------------------------------------------------------------------------
    def __add__(self, o):
        return _binop("add", self, o)

    def __radd__(self, o):
        return _binop("add", o, self)

    def __sub__(self, o):
        return _binop("sub", self, o)

    def __rsub__(self, o):
        return _binop("sub", o, self)

    def __mul__(self, o):
        return _binop("mul", self, o)

    def __rmul__(self, o):
        return _binop("mul", o, self)

    def __truediv__(self, o):
        return _binop("div", self, o)

    def __rtruediv__(self, o):
        return _binop("div", o, self)

    def __neg__(self):
        return TS(-self.s, "rate", 1, [f"unary minus on {self.kind} -> rate"])

    def __pos__(self):
        return self


def _is_num(x) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def _kind_of(x) -> str:
    return "scalar" if _is_num(x) else x.kind


def _arith_kind(k: str) -> str:
    return "count" if k == "event" else k  # events behave like counts in arithmetic


def _binop(op: str, a, b) -> TS | float:
    if _is_num(a) and _is_num(b):
        return _scalar_op(op, float(a), float(b))
    ta = a if isinstance(a, TS) else None
    tb = b if isinstance(b, TS) else None
    ka, kb = _kind_of(a), _kind_of(b)
    scale = 1
    if ta is not None and tb is not None:
        if not ta.s.index.equals(tb.s.index):
            raise AlignmentError("operands are not on the same grid (internal error)")
        sa, sb = ta.s, tb.s
    else:
        sa = ta.s if ta is not None else float(a)
        sb = tb.s if tb is not None else float(b)
    if op == "add":
        out = sa + sb
    elif op == "sub":
        out = sa - sb
    elif op == "mul":
        out = sa * sb
    else:
        if isinstance(sb, pd.Series):
            sb = sb.where(sb != 0)  # x / 0 is undefined -> missing (never inf, never 0)
        elif sb == 0:
            sb = np.nan
        out = sa / sb
    out = _finite(out)
    ma = a.mult if isinstance(a, TS) else 1.0
    mb = b.mult if isinstance(b, TS) else 1.0
    kind, scale, note = _infer(op, a, b, ka, kb, ma / mb)
    mult = 1.0
    if kind == "count":
        if op == "mul" and _is_num(b):
            mult = ma * float(b)
        elif op == "mul" and _is_num(a):
            mult = mb * float(a)
        elif op == "add" and ma == mb:
            mult = ma
    notes = [n for t in (ta, tb) if t is not None for n in t.notes] + [note]
    return TS(out, kind, scale, notes, mult)


def _scalar_op(op: str, a: float, b: float) -> float:
    if op == "add":
        r = a + b
    elif op == "sub":
        r = a - b
    elif op == "mul":
        r = a * b
    else:
        if b == 0:
            raise ExpressionError("division of constants by zero")
        r = a / b
    if not math.isfinite(r) or abs(r) > MAX_CONST:
        raise ExpressionError("constant out of range")
    return r


def _finite(x):
    if isinstance(x, pd.Series) and np.isinf(x.to_numpy(dtype=float)).any():
        raise ExpressionError("numeric overflow (infinite value) in expression")
    return x


def _scale_of(x) -> int:
    return x.scale if isinstance(x, TS) else 1


def _infer(op: str, a, b, ka: str, kb: str, ratio: float = 1.0) -> tuple[str, int, str]:
    """Operator-level kind inference. Returns (kind, proportion_scale, note)."""
    A, B = _arith_kind(ka), _arith_kind(kb)
    both_events = ka == kb == "event"
    sym = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[op]
    desc = f"{ka} {sym} {kb}"

    def r(kind: str, scale: int = 1, why: str = "") -> tuple[str, int, str]:
        return kind, scale, f"{desc} -> {kind}" + (f" ({why})" if why else "")

    if op == "add":
        if A == B == "count":
            return r("event" if both_events else "count")
        return r("rate", why="mixed or non-additive kinds")
    if op == "sub":
        return r("rate", why="a difference can be negative, so it is not a count")
    if op == "mul":
        if "scalar" in (A, B):
            if B == "scalar":
                k, x, sc = A, b, _scale_of(a)
            else:
                k, x, sc = B, a, _scale_of(b)
            if k == "count" and x > 0:
                return r("count")
            if k == "proportion" and sc == 1 and x == 100:
                return r("proportion", 100, "fraction -> percent")
            if k == "proportion" and sc == 100 and x == 0.01:
                return r("proportion", 1, "percent -> fraction")
            return r("rate")
        if {A, B} == {"proportion", "count"}:
            return r("count", why="proportion x denominator")
        if A == B == "proportion" and _scale_of(a) == _scale_of(b) == 1:
            return r("proportion")
        return r("rate")
    # division
    if A == B == "count":
        if abs(ratio - 1.0) < 1e-12:
            return r("proportion", 1, "ratio of counts; verified to lie in [0, 1]")
        if abs(ratio - 100.0) < 1e-12:
            return r("proportion", 100, "100 x ratio of counts; verified to lie in [0, 100]")
        return r("rate", why="ratio of rescaled counts")
    if B == "scalar" and A == "proportion":
        sc, x = _scale_of(a), b
        if sc == 100 and x == 100:
            return r("proportion", 1, "percent / 100 -> fraction")
    return r("rate")


# --------------------------------------------------------------------------------------
# functions
# --------------------------------------------------------------------------------------
def _need_ts(x, fn: str) -> TS:
    if not isinstance(x, TS):
        raise ExpressionError(f"{fn}() needs a series argument")
    return x


def _need_n(x, fn: str, default: int | None = None) -> int:
    if x is None and default is not None:
        return default
    if isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= MAX_WINDOW:
        return x
    raise ExpressionError(f"{fn}(): window/lag must be an integer constant in 1..{MAX_WINDOW}")


def _rolling(x: TS, n: int, how: str, fn: str) -> TS:
    if x.kind == "proportion":
        raise ExpressionError(
            f"{fn}() of a proportion is not allowed (a plain mean/sum of proportions is wrong: "
            "weeks have different denominators). Use rolling_ratio(numerator, denominator, n) "
            "or sum(num...)/sum(den...)  (ratio of sums)."
        )
    roll = x.s.rolling(n, min_periods=n)  # any missing week in the window -> missing
    out = roll.sum() if how == "sum" else roll.mean()
    kind = x.kind
    return TS(out, kind, 1, [*x.notes, f"{fn}({x.kind}, {n}) -> {kind}"], x.mult)


def f_rolling_sum(x, n):
    return _rolling(_need_ts(x, "rolling_sum"), _need_n(n, "rolling_sum"), "sum", "rolling_sum")


def f_rolling_mean(x, n):
    return _rolling(_need_ts(x, "rolling_mean"), _need_n(n, "rolling_mean"), "mean", "rolling_mean")


def f_rolling_ratio(num, den, n):
    num, den = _need_ts(num, "rolling_ratio"), _need_ts(den, "rolling_ratio")
    n = _need_n(n, "rolling_ratio")
    for t in (num, den):
        if t.kind == "proportion":
            raise ExpressionError("rolling_ratio() needs counts/rates, not proportions")
    return _binop(
        "div", _rolling(num, n, "sum", "rolling_sum"), _rolling(den, n, "sum", "rolling_sum")
    )


def f_sum(*xs):
    if len(xs) < 2:
        raise ExpressionError("sum() needs at least 2 series")
    if not all(isinstance(x, TS) for x in xs):
        raise ExpressionError("sum() takes series only")
    if any(x.kind == "proportion" for x in xs):
        raise ExpressionError(
            "sum() of proportions is not allowed; use ratio of sums: sum(num...) / sum(den...)"
        )
    out = xs[0]
    for x in xs[1:]:
        out = _binop("add", out, x)  # NaN if any term missing (no zero-fill)
    return out


def f_lag(x, n=1):
    x = _need_ts(x, "lag")
    n = _need_n(n, "lag", 1)
    return TS(x.s.shift(n), x.kind, x.scale, [*x.notes, f"lag({x.kind}, {n}) -> {x.kind}"], x.mult)


def f_diff(x, n=1):
    x = _need_ts(x, "diff")
    n = _need_n(n, "diff", 1)
    return TS(x.s.diff(n), "rate", 1, [*x.notes, f"diff({x.kind}, {n}) -> rate"])


def f_pct_change(x, n=1):
    x = _need_ts(x, "pct_change")
    n = _need_n(n, "pct_change", 1)
    base = x.s.shift(n).where(lambda v: v != 0)  # division by zero -> missing
    out = 100.0 * (x.s / base - 1.0)
    return TS(out, "rate", 1, [*x.notes, f"pct_change({x.kind}, {n}) -> rate (percent)"])


def f_abs(x):
    x = _need_ts(x, "abs")
    kind = "count" if x.kind == "count" else ("event" if x.kind == "event" else "rate")
    return TS(x.s.abs(), kind, 1, [*x.notes, f"abs({x.kind}) -> {kind}"])


def f_log1p(x):
    x = _need_ts(x, "log1p")
    if (x.s.dropna() <= -1).any():
        raise ExpressionError("log1p() of values <= -1")
    return TS(np.log1p(x.s), "rate", 1, [*x.notes, f"log1p({x.kind}) -> rate"])


def f_sqrt(x):
    x = _need_ts(x, "sqrt")
    if (x.s.dropna() < 0).any():
        raise ExpressionError("sqrt() of negative values")
    return TS(np.sqrt(x.s), "rate", 1, [*x.notes, f"sqrt({x.kind}) -> rate"])


FUNCTIONS: dict[str, Callable] = {
    "rolling_sum": f_rolling_sum,
    "rolling_mean": f_rolling_mean,
    "ma": f_rolling_mean,  # alias
    "rolling_ratio": f_rolling_ratio,
    "sum": f_sum,
    "lag": f_lag,
    "diff": f_diff,
    "pct_change": f_pct_change,
    "pct": f_pct_change,  # alias
    "abs": f_abs,
    "log1p": f_log1p,
    "sqrt": f_sqrt,
}
ARITY: dict[str, tuple[int, int]] = {
    "rolling_sum": (2, 2),
    "rolling_mean": (2, 2),
    "ma": (2, 2),
    "rolling_ratio": (3, 3),
    "sum": (2, MAX_SOURCES),
    "lag": (1, 2),
    "diff": (1, 2),
    "pct_change": (1, 2),
    "pct": (1, 2),
    "abs": (1, 1),
    "log1p": (1, 1),
    "sqrt": (1, 1),
}


# --------------------------------------------------------------------------------------
# parsing (whitelist walk) and evaluation (simpleeval, stripped)
# --------------------------------------------------------------------------------------
_BIN_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_UN_OPS = (ast.UAdd, ast.USub)


def parse_expression(expr: str) -> ast.Expression:
    """Parse and validate. Raises :class:`ExpressionError` for anything outside the grammar."""
    if not isinstance(expr, str) or not expr.strip():
        raise ExpressionError("expression must be a non-empty string")
    if len(expr) > MAX_EXPR_LEN:
        raise ExpressionError(f"expression longer than {MAX_EXPR_LEN} characters")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"syntax error: {exc.msg}") from exc
    except (ValueError, RecursionError, MemoryError) as exc:  # NUL bytes, absurd nesting
        raise ExpressionError("unparseable expression") from exc
    n = 0
    for node in ast.walk(tree):
        n += 1
        if n > MAX_NODES:
            raise ExpressionError("expression too complex")
        _check_node(node)
    return tree


def _check_node(node: ast.AST) -> None:
    if isinstance(node, ast.Expression | ast.Load):
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _BIN_OPS):
            raise ExpressionError(f"operator {type(node.op).__name__} is not allowed (+ - * /)")
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _UN_OPS):
            raise ExpressionError(f"operator {type(node.op).__name__} is not allowed")
        return
    if isinstance(node, _BIN_OPS + _UN_OPS):
        return
    if isinstance(node, ast.Name):
        return
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise ExpressionError("only numeric constants are allowed")
        if not math.isfinite(v) or abs(v) > MAX_CONST:
            raise ExpressionError("numeric constant out of range")
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExpressionError("only plain function calls are allowed")
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise ExpressionError("keyword/star arguments are not allowed")
        name = node.func.id
        if name not in FUNCTIONS:
            raise ExpressionError(f"unknown function {name!r}; allowed: {sorted(FUNCTIONS)}")
        lo, hi = ARITY[name]
        if not lo <= len(node.args) <= hi:
            raise ExpressionError(f"{name}() takes {lo}..{hi} arguments, got {len(node.args)}")
        return
    raise ExpressionError(f"disallowed syntax: {type(node).__name__}")


def referenced_names(expr: str) -> list[str]:
    """Series names (aliases or ids) used in ``expr``, sorted, excluding function names."""
    tree = parse_expression(expr)
    funcs = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - funcs)


class _SafeEval(simpleeval.SimpleEval):
    """simpleeval with every node type removed except the whitelist."""

    def __init__(self, names: dict[str, TS]):
        super().__init__(
            operators={
                ast.Add: lambda a, b: a + b,
                ast.Sub: lambda a, b: a - b,
                ast.Mult: lambda a, b: a * b,
                ast.Div: lambda a, b: a / b,
                ast.UAdd: lambda a: +a,
                ast.USub: lambda a: -a,
            },
            functions=dict(FUNCTIONS),
            names=names,
        )
        keep = {
            ast.Name: self.nodes[ast.Name],
            ast.UnaryOp: self.nodes[ast.UnaryOp],
            ast.BinOp: self.nodes[ast.BinOp],
            ast.Call: self.nodes[ast.Call],
            ast.Constant: self.nodes[ast.Constant],
        }
        self.nodes = keep


def evaluate(expr: str, inputs: dict[str, TS]) -> TS:
    """Evaluate ``expr`` over typed series (already aligned to one grid)."""
    tree = parse_expression(expr)
    try:
        out = _SafeEval(dict(inputs)).eval(expr, previously_parsed=tree.body)
    except ExpressionError:
        raise
    except simpleeval.NameNotDefined as exc:
        raise ExpressionError(f"unknown series {exc.name!r}") from None
    except (simpleeval.InvalidExpression, simpleeval.FeatureNotAvailable) as exc:
        raise ExpressionError(f"disallowed expression: {exc}") from None
    except AlignmentError:
        raise
    except (TypeError, ValueError, ArithmeticError, RecursionError) as exc:
        raise ExpressionError(f"cannot evaluate: {type(exc).__name__}") from None
    if not isinstance(out, TS):
        raise ExpressionError("expression must involve at least one series")
    return _finalise(out)


def _finalise(t: TS) -> TS:
    """Post-hoc validation of the inferred kind against the actual values."""
    v = t.s.dropna()
    kind, scale, notes = t.kind, t.scale, list(t.notes)
    if kind == "proportion" and len(v) and ((v < -1e-9) | (v > scale + 1e-9)).any():
        notes.append(f"proportion left [0, {scale}] -> downgraded to rate")
        kind, scale = "rate", 1
    if kind in ("count", "event") and len(v) and (v < -1e-9).any():
        notes.append(f"{kind} became negative -> downgraded to rate")
        kind = "rate"
    return TS(t.s, kind, scale, notes, t.mult)


# --------------------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------------------
def align(
    frames: dict[str, pd.DataFrame], mode: str = "intersection"
) -> tuple[dict[str, pd.Series], dict]:
    """Put several ``ds, y`` frames on one weekly grid. Missing weeks are NaN (never 0).

    ``mode``: ``intersection`` (default) = overlap of all sources' date ranges; ``union`` = from
    the earliest start to the latest end (weeks outside a source's own range are missing).
    """
    if mode not in ("intersection", "union"):
        raise AlignmentError("align must be 'intersection' or 'union'")
    if not frames:
        raise AlignmentError("no source series")
    ref = None
    spans = {}
    for name, df in frames.items():
        if df.empty:
            raise AlignmentError(f"source {name!r} has no observations")
        ds = pd.to_datetime(df["ds"])
        if ref is None:
            ref = ds.min()
        if ((ds - ref).dt.days % 7 != 0).any():
            raise AlignmentError(
                f"source {name!r} is not on the same weekly grid (week-start weekday differs)"
            )
        spans[name] = (ds.min(), ds.max())
    starts = [a for a, _ in spans.values()]
    ends = [b for _, b in spans.values()]
    lo, hi = (max(starts), min(ends)) if mode == "intersection" else (min(starts), max(ends))
    if lo > hi:
        raise AlignmentError("sources have no overlapping period")
    idx = pd.date_range(lo, hi, freq="7D")
    out: dict[str, pd.Series] = {}
    per_source = {}
    for name, df in frames.items():
        s = df.assign(ds=pd.to_datetime(df["ds"])).set_index("ds")["y"].astype(float)
        aligned = s.reindex(idx)
        out[name] = aligned
        per_source[name] = {
            "first": spans[name][0].date().isoformat(),
            "last": spans[name][1].date().isoformat(),
            "n_obs": int(len(s)),
            "n_missing_in_grid": int(aligned.isna().sum()),
            "n_outside_grid": int(((s.index < lo) | (s.index > hi)).sum()),
        }
    report = {
        "mode": mode,
        "grid_start": lo.date().isoformat(),
        "grid_end": hi.date().isoformat(),
        "n_periods": int(len(idx)),
        "per_source": per_source,
    }
    return out, report
