"""Backtest, kind-specific transforms/bounds and policy decisions (fast: season length 13)."""

import numpy as np
import pytest

from ailab import policy as policy_mod
from ailab.forecast import (
    rolling_origin_backtest,
    windows_for_seasons,
)
from ailab.pipeline import analyse_frame
from ailab.transforms import for_kind

from .conftest import SL, count_series, flat_series, meta, prop_series, sparse_series

FAST = dict(horizon=4, n_windows=14, step=3)


def test_backtest_has_no_leakage_and_correct_horizons(seasonal):
    bt = rolling_origin_backtest(seasonal, SL, horizon=4, n_windows=8, step=2)
    assert bt is not None and bt.n_windows == 8
    cv = bt.cv
    assert (cv["ds"] > cv["cutoff"]).all()  # every target is strictly after its origin
    assert set(cv["h"]) == {1, 2, 3, 4}
    truth = seasonal.set_index("ds")["y"]
    assert np.allclose(cv["y"].to_numpy(), truth.loc[cv["ds"]].to_numpy())  # original units


def test_backtest_none_when_too_short():
    assert rolling_origin_backtest(count_series(2), SL, 8, 26, 2) is None


def test_default_backtest_window_spans_at_least_three_seasons():
    assert windows_for_seasons(3, 52, 3) == 52 and windows_for_seasons(3, 13, 3) == 13
    res = analyse_frame(meta(), count_series(10), horizon=4)  # defaults: step 3, 3 seasons
    bt = res["backtest"]
    assert bt["seasons_covered"] >= 3.0 and bt["n_windows"] == 13
    assert bt["transform"] == "log1p"


def test_seasonal_count_series_allowed_with_band_nonnegative(seasonal):
    res = analyse_frame(meta(), seasonal, **FAST)
    pol = res["policy"]
    assert pol["forecast_allowed"] is True and pol["horizon_weeks"] >= 1
    assert pol["indicator_type"] == "count"
    assert pol["model_family"] in ("mstl_ets", "theta", "naive")
    fc = res["forecast"]
    assert fc["transform"] == "log1p" and len(fc["points"]) == pol["horizon_weeks"]
    for p in fc["points"]:
        assert 0 <= p["lo95"] <= p["lo80"] <= p["point"] <= p["hi80"] <= p["hi95"]
    # asymmetric on the original scale (log space is symmetric): upper tail is longer
    p = fc["points"][0]
    assert (p["hi95"] - p["point"]) > (p["point"] - p["lo95"])
    assert res["backtest"]["baseline"] == "SeasonalNaive"


def test_policy_contract_fields_present(seasonal):
    pol = analyse_frame(meta(), seasonal, **FAST)["policy"]
    assert {
        "forecast_allowed",
        "reason",
        "display_mode",
        "model_family",
        "horizon_weeks",
        "indicator_type",
        "warnings",
    } <= set(pol)


def test_policy_denies_short_event_and_sparse():
    short = analyse_frame(meta(season_length=52), count_series(4), horizon=4)
    assert short["policy"]["forecast_allowed"] is False
    assert short["policy"]["reason"] == "history_lt_3_seasons" and short["forecast"] is None
    ev = analyse_frame(meta(itype="event"), count_series(8), horizon=4)
    assert ev["policy"]["reason"] == "indicator_type_event_sparse"
    assert ev["policy"]["display_mode"] == "monitor_only" and ev["alerts"]  # alerts still run
    sp = analyse_frame(meta(), sparse_series(), horizon=4)
    assert sp["policy"]["reason"] == "sparse_series"


def test_policy_denies_long_gaps_and_negative_values():
    df = count_series(8)
    df.loc[40:47, "y"] = np.nan  # 8 missing weeks
    res = analyse_frame(meta(), df, **FAST)
    assert res["policy"]["reason"] == "data_gaps_too_long"
    assert res["policy"]["display_mode"] == "history_only"
    neg = count_series(8)
    neg.loc[10, "y"] = -3
    assert analyse_frame(meta("n", "rate"), neg, **FAST)["policy"]["reason"] == "negative_values"


def test_short_gaps_are_interpolated_for_model_only_and_disclosed():
    df = count_series(8)
    df.loc[[30, 31], "y"] = np.nan
    res = analyse_frame(meta(), df, **FAST)
    assert res["model_input"]["interpolated_points"] == 2
    obs = {o["ds"]: o["y"] for o in res["observations"]}
    assert sum(v is None for v in obs.values()) == 2  # still shown as missing, not 0


def test_noise_series_is_denied_or_only_where_skilled():
    res = analyse_frame(meta(), flat_series(13 * 8, seed=1), **FAST)
    pol = res["policy"]
    if pol["forecast_allowed"]:
        best = res["backtest"]["chosen_model"]
        assert res["backtest"]["models"][best]["rel_mae_vs_seasonal_naive"] < 1.0
    else:
        assert pol["reason"] == "no_skill_vs_seasonal_naive"


def test_count_forecast_is_never_negative_even_for_small_counts():
    df = count_series(8, level=1.5, amplitude=2.0, seed=2)
    res = analyse_frame(meta(), df, **FAST)
    if res["forecast"]:
        for p in res["forecast"]["points"]:
            assert all(v >= 0 for k, v in p.items() if k not in ("h", "ds"))


@pytest.mark.parametrize("scale", [1, 100])
def test_proportion_forecast_is_inside_bounds_and_uses_logit(scale):
    df = prop_series(10, seed=1, level=0.45, amplitude=0.9, value_scale=scale)
    res = analyse_frame(meta(itype="proportion", value_scale=scale), df, horizon=4, step=3)
    assert res["backtest"]["transform"] == "logit"
    assert res["policy"]["forecast_allowed"] is True  # strong seasonal signal passes the gate
    assert res["forecast"]["transform"] == "logit"
    for p in res["forecast"]["points"]:
        vals = [v for k, v in p.items() if k not in ("h", "ds")]
        assert all(0 <= v <= scale for v in vals)


def test_logit_intervals_are_bounded_near_the_ceiling():
    # a series hugging the upper bound: symmetric intervals would exceed 1, logit ones cannot
    df = prop_series(10, seed=3, level=0.9, amplitude=0.05, denominator=300)
    res = analyse_frame(meta(itype="proportion"), df, horizon=4, step=3)
    assert res["policy"]["display_mode"] == "forecast_band"
    for p in res["forecast"]["points"]:
        assert p["hi95"] <= 1.0 and p["lo95"] >= 0.0


def test_proportion_can_be_switched_off():
    from ailab.policy import decide

    pol, model = decide(
        "proportion", {"checks": [], "grade": "A", "reasons": []}, None, None, 4, False
    )
    assert pol.forecast_allowed is False and model is None
    assert pol.reason == "indicator_type_proportion_monitor_only"


# ---- policy rules in isolation (stubbed backtest) -------------------------------------
class _BT:
    def __init__(self, rel=0.7, flags=None, seasons=3.2, horizon=4):
        flags = flags or {1: True, 2: True, 3: True, 4: True}
        self.horizon = horizon
        self.seasons_covered = seasons
        self.metrics = {
            m: {
                "mae": 1.0 if m != "MSTL" else rel,
                "rel_mae_vs_seasonal_naive": rel,
                "beats_baseline_by_horizon": flags,
            }
            for m in ("SeasonalNaive", "Naive", "MSTL", "AutoTheta")
        }


GOOD_Q = {"checks": [], "grade": "A", "reasons": []}
GOOD_CAL = {"80": {"empirical_loo": 0.85}, "95": {"empirical_loo": 0.95}}


def test_policy_proportion_stricter_than_count():
    rel_bad = _BT(rel=0.97)  # beats the baseline by only 3 %
    c, _ = policy_mod.decide("count", GOOD_Q, rel_bad, GOOD_CAL, 4)
    p, _ = policy_mod.decide("proportion", GOOD_Q, rel_bad, GOOD_CAL, 4)
    assert c.forecast_allowed is True
    assert p.forecast_allowed is False and p.reason == "no_skill_vs_seasonal_naive"


def test_policy_proportion_requires_two_horizons_and_three_season_backtest_and_coverage():
    one_h = _BT(flags={1: True, 2: False, 3: False, 4: False})
    assert policy_mod.decide("count", GOOD_Q, one_h, GOOD_CAL, 4)[0].forecast_allowed
    p = policy_mod.decide("proportion", GOOD_Q, one_h, GOOD_CAL, 4)[0]
    assert not p.forecast_allowed and p.reason == "skill_horizon_too_short"
    short = _BT(seasons=1.5)
    c = policy_mod.decide("count", GOOD_Q, short, GOOD_CAL, 4)[0]
    assert c.forecast_allowed and any("seasons" in w for w in c.warnings)
    p = policy_mod.decide("proportion", GOOD_Q, short, GOOD_CAL, 4)[0]
    assert not p.forecast_allowed and p.reason == "backtest_lt_3_seasons"
    poor_cal = {"80": {"empirical_loo": 0.65}}
    c = policy_mod.decide("count", GOOD_Q, _BT(), poor_cal, 4)[0]
    assert c.forecast_allowed and c.display_mode == "forecast_band"  # 0.65 >= 0.6
    p = policy_mod.decide("proportion", GOOD_Q, _BT(), poor_cal, 4)[0]
    assert not p.forecast_allowed and p.reason == "band_undercoverage_in_backtest"
    very_poor = {"80": {"empirical_loo": 0.4}}
    c = policy_mod.decide("count", GOOD_Q, _BT(), very_poor, 4)[0]
    assert c.forecast_allowed and c.display_mode == "forecast_point_only"


def test_policy_good_proportion_is_allowed_and_horizon_truncated():
    p, model = policy_mod.decide("proportion", GOOD_Q, _BT(horizon=4), GOOD_CAL, 8)
    assert p.forecast_allowed and model == "MSTL" and p.horizon_weeks == 4
    assert any("truncated" in w for w in p.warnings)
    assert policy_mod.decide("rate", GOOD_Q, _BT(), GOOD_CAL, 2)[0].horizon_weeks == 2


def test_transform_matches_kind_in_backtest_metrics_are_original_scale():
    df = count_series(8, seed=4)
    bt = rolling_origin_backtest(df, SL, 4, 8, 3, for_kind("count"))
    err = (bt.cv["y"] - bt.cv["SeasonalNaive"]).abs().mean()
    assert bt.metrics["SeasonalNaive"]["mae"] == pytest.approx(err)
    # seasonal naive back-transformed equals the observation one season earlier
    row = bt.cv.iloc[0]
    prev = df.set_index("ds")["y"].loc[row["ds"] - np.timedelta64(7 * SL, "D")]
    assert row["SeasonalNaive"] == pytest.approx(prev, rel=1e-6)
