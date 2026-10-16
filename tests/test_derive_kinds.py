"""Kind inference, missing-value semantics and ratio-of-sums rules."""

import numpy as np
import pytest

from ailab import derive
from ailab.derive import TS, ExpressionError

from .conftest import ser


def mk(vals, idx, kind="count", scale=1):
    return TS(ser(vals, idx), kind, scale)


@pytest.fixture
def env(idx):
    return {
        "c1": mk([1, 2, 3, 4, 5, 6], idx),
        "c2": mk([2, 4, 6, 8, 10, 12], idx),
        "p1": mk([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], idx, "proportion"),
        "p2": mk([0.5, 0.5, 0.5, 0.5, 0.5, 0.5], idx, "proportion"),
        "pc": mk([10, 20, 30, 40, 50, 60], idx, "proportion", 100),
        "r1": mk([1.5, 2.5, 3.5, 4.5, 5.5, 6.5], idx, "rate"),
        "e1": mk([0, 1, 0, 2, 0, 1], idx, "event"),
        "e2": mk([1, 1, 0, 0, 1, 0], idx, "event"),
    }


@pytest.mark.parametrize(
    "expr,kind",
    [
        ("c1 + c2", "count"),
        ("c1 + c2 + c1", "count"),
        ("sum(c1, c2)", "count"),
        ("c1 * 3", "count"),
        ("3 * c1", "count"),
        ("rolling_sum(c1, 3)", "count"),
        ("rolling_mean(c1, 3)", "count"),
        ("lag(c1, 1)", "count"),
        ("abs(c1)", "count"),
        ("c1 / c2", "proportion"),
        ("rolling_ratio(c1, c2, 2)", "proportion"),
        ("c1 - c2", "rate"),
        ("diff(c1)", "rate"),
        ("pct_change(c1)", "rate"),
        ("c1 * c2", "rate"),
        ("c1 / 1000", "rate"),
        ("c1 + r1", "rate"),
        ("c1 + 5", "rate"),
        ("r1 * 2", "rate"),
        ("sqrt(c1)", "rate"),
        ("log1p(c1)", "rate"),
        ("-c1", "rate"),
        ("p1 * c1", "count"),  # proportion x denominator -> expected count
        ("c1 * p1", "count"),
        ("p1 * p2", "proportion"),
        ("p1 + p2", "rate"),  # sum of proportions is not a proportion
        ("p1 / p2", "rate"),
        ("c1 / p1", "rate"),
        ("lag(p1, 1)", "proportion"),  # a pure shift keeps the kind
        ("e1 + e2", "event"),
        ("e1 + c1", "count"),
        ("e1 / e2", "proportion"),  # events behave like counts in arithmetic
    ],
)
def test_kind_inference(expr, kind, env):
    r = derive.evaluate(expr, env)
    assert r.kind == kind, (expr, r.notes)


def test_ratio_of_counts_scale_conversion(env):
    r = derive.evaluate("c1 / c2 * 100", env)
    assert r.kind == "proportion" and r.scale == 100
    assert r.s.iloc[0] == pytest.approx(50.0)
    back = derive.evaluate("c1 / c2 * 100 / 100", env)
    assert back.kind == "proportion" and back.scale == 1
    r2 = derive.evaluate("pc / 100", env)
    assert r2.kind == "proportion" and r2.scale == 1
    assert derive.evaluate("p1 * 100", env).scale == 100
    assert derive.evaluate("p1 * 50", env).kind == "rate"  # not a unit conversion


def test_proportion_outside_bounds_is_downgraded_to_rate(env, idx):
    # c2 / c1 = 2 -> cannot be a proportion
    r = derive.evaluate("c2 / c1", env)
    assert r.kind == "rate" and any("downgraded" in n for n in r.notes)
    # percent that exceeds 100
    assert derive.evaluate("c2 / c1 * 100", env).kind == "rate"


def test_count_going_negative_is_downgraded(env):
    r = derive.evaluate("abs(c1) + 0", env)
    assert r.kind == "rate"  # +scalar already makes it a rate
    r = derive.evaluate("c1 * 1", env)
    assert r.kind == "count"


def test_plain_mean_or_sum_of_proportions_is_rejected(env):
    for expr in (
        "rolling_mean(p1, 3)",
        "ma(p1, 3)",
        "rolling_sum(p1, 3)",
        "sum(p1, p2)",
        "rolling_mean(c1 / c2, 3)",
    ):
        with pytest.raises(ExpressionError, match="ratio of sums|not allowed"):
            derive.evaluate(expr, env)


def test_ratio_of_sums_differs_from_mean_of_ratios(idx):
    num = mk([1, 90, 5, 5, 5, 5], idx)
    den = mk([100, 100, 10000, 10000, 10000, 10000], idx)
    env = {"n": num, "d": den}
    ros = derive.evaluate("rolling_ratio(n, d, 2)", env).s.iloc[2]
    assert ros == pytest.approx((90 + 5) / (100 + 10000))
    mean_of_ratios = np.mean([90 / 100, 5 / 10000])
    assert abs(ros - mean_of_ratios) > 0.01  # the wrong (plain-mean) answer is different
    s2 = derive.evaluate("sum(n, n) / sum(d, d)", env).s.iloc[0]
    assert s2 == pytest.approx(1 / 100)


def test_missing_propagates_and_is_never_zero(idx):
    a = mk([1, np.nan, 3, 4, 5, 6], idx)
    b = mk([2, 2, np.nan, 2, 2, 2], idx)
    env = {"a": a, "b": b}
    assert np.isnan(derive.evaluate("a + b", env).s.iloc[[1, 2]]).all()
    assert np.isnan(derive.evaluate("sum(a, b)", env).s.iloc[[1, 2]]).all()  # not 0-filled
    assert np.isnan(derive.evaluate("a - b", env).s.iloc[[1, 2]]).all()
    assert np.isnan(derive.evaluate("a * b", env).s.iloc[[1, 2]]).all()
    r = derive.evaluate("rolling_sum(a, 2)", env).s
    assert np.isnan(r.iloc[:3]).all() and r.iloc[3] == 7  # window with a missing week -> missing
    assert np.isnan(derive.evaluate("lag(a, 1)", env).s.iloc[[0, 2]]).all()
    assert np.isnan(derive.evaluate("diff(a)", env).s.iloc[[0, 1, 2]]).all()


def test_division_by_zero_is_missing_not_inf_or_zero(idx):
    a = mk([1, 2, 3, 4, 5, 6], idx)
    z = mk([2, 0, 2, 0, 2, 2], idx)
    r = derive.evaluate("a / z", {"a": a, "z": z}).s
    assert np.isnan(r.iloc[[1, 3]]).all() and np.isfinite(r.dropna()).all()
    assert np.isnan(derive.evaluate("a / 0", {"a": a}).s).all()
    with pytest.raises(ExpressionError):
        derive.evaluate("a + 1 / 0", {"a": a})  # constant division by zero
    pc = derive.evaluate("pct_change(z)", {"z": z}).s
    assert np.isnan(pc.iloc[[2, 4]]).all()  # previous value 0 -> undefined


def test_overflow_is_an_error(idx):
    a = mk([1e300] * 6, idx)
    with pytest.raises(ExpressionError, match="overflow"):
        derive.evaluate("a * a", {"a": a})


def test_scalar_only_expression_rejected(env):
    with pytest.raises(ExpressionError):
        derive.evaluate("2 * 3 + 1", env)


def test_percent_written_naturally(env):
    for expr in ("100 * c1 / c2", "c1 * 100 / c2", "100 * sum(c1, c2) / sum(c2, c2)"):
        r = derive.evaluate(expr, env)
        assert r.kind == "proportion" and r.scale == 100, (expr, r.notes)
    assert derive.evaluate("100 * c1 / (100 * c2)", env).scale == 1
    assert derive.evaluate("1000 * c1 / c2", env).kind == "rate"
    assert derive.evaluate("100 * c2 / c1", env).kind == "rate"  # > 100 %: downgraded
