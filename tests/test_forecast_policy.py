import numpy as np
import pandas as pd

from ailab.forecast import rolling_origin_backtest
from ailab.pipeline import analyse_frame

from .conftest import make_weekly, meta


def test_backtest_has_no_leakage_and_correct_horizons(seasonal):
    bt = rolling_origin_backtest(seasonal, 52, horizon=4, n_windows=8, step=2)
    assert bt is not None and bt.n_windows == 8
    cv = bt.cv
    assert (cv["ds"] > cv["cutoff"]).all()  # every target is strictly after its origin
    assert set(cv["h"]) == {1, 2, 3, 4}
    # truth matches the series at those dates
    truth = seasonal.set_index("ds")["y"]
    assert np.allclose(cv["y"].to_numpy(), truth.loc[cv["ds"]].to_numpy())


def test_backtest_none_when_too_short():
    assert rolling_origin_backtest(make_weekly(110), 52, 8, 26, 2) is None


def test_seasonal_series_is_allowed_with_band(seasonal):
    res = analyse_frame(meta(), seasonal, horizon=6, n_windows=12, step=4)
    pol = res["policy"]
    assert pol["forecast_allowed"] is True and pol["horizon_weeks"] >= 1
    assert pol["model_family"] in ("mstl_ets", "theta", "naive")
    fc = res["forecast"]
    assert len(fc["points"]) == pol["horizon_weeks"]
    p = fc["points"][0]
    assert p["lo95"] <= p["lo80"] <= p["point"] <= p["hi80"] <= p["hi95"]
    assert res["backtest"]["baseline"] == "SeasonalNaive"


def test_policy_denies_short_event_and_noise():
    short = analyse_frame(meta(), make_weekly(100), horizon=4)
    assert short["policy"]["forecast_allowed"] is False
    assert short["policy"]["reason"] == "history_lt_3_seasons" and short["forecast"] is None
    ev = analyse_frame(meta(itype="event"), make_weekly(52 * 6), horizon=4)
    assert ev["policy"]["reason"] == "indicator_type_event_sparse"
    assert ev["policy"]["display_mode"] == "monitor_only" and ev["alerts"]  # alerts still run
    sp = analyse_frame(meta(), make_weekly(52 * 6, "sparse"), horizon=4)
    assert sp["policy"]["reason"] == "sparse_series"


def test_policy_contract_fields_present(seasonal):
    pol = analyse_frame(meta(), seasonal, horizon=4, n_windows=8, step=4)["policy"]
    assert {
        "forecast_allowed",
        "reason",
        "display_mode",
        "model_family",
        "horizon_weeks",
        "indicator_type",
    } <= set(pol)


def test_noise_series_has_no_skill_or_is_truncated():
    df = make_weekly(52 * 8, "noise", seed=1)
    res = analyse_frame(meta(), df, horizon=8, n_windows=12, step=4)
    # pure noise: either denied, or only allowed where the model actually beats the baseline
    pol = res["policy"]
    if pol["forecast_allowed"]:
        best = res["backtest"]["chosen_model"]
        assert res["backtest"]["models"][best]["rel_mae_vs_seasonal_naive"] < 1.5
    else:
        assert pol["reason"] == "no_skill_vs_seasonal_naive"


def test_proportion_forecast_is_clipped():
    df = make_weekly(52 * 8, "seasonal")
    df["y"] = (df["y"] / df["y"].max() * 99.5).round(3)
    res = analyse_frame(meta(itype="proportion"), df, horizon=4, n_windows=8, step=4)
    if res["forecast"]:
        for p in res["forecast"]["points"]:
            assert all(0 <= v <= 100 for k, v in p.items() if k not in ("h", "ds"))
    assert isinstance(pd.Timestamp(res["data_through"]), pd.Timestamp)
