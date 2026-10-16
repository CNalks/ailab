import numpy as np
import pandas as pd

from ailab import alerts, quality

from .conftest import make_weekly, meta


def _flat_with_spike():
    ds = pd.date_range("2024-01-07", periods=40, freq="7D")
    y = np.full(40, 20.0) + np.tile([0, 1, -1, 2, -2], 8)
    y[30] = 80.0
    return pd.DataFrame({"ds": ds, "y": y})


def test_ears_and_cusum_signal_on_spike_only():
    df = _flat_with_spike()
    for method in ("ears_c1", "ears_c2", "ears_c3", "cusum"):
        res = alerts.detect(df, method)
        sig = res.loc[res["alarm"], "ds"]
        assert len(sig) >= 1, method
        assert sig.min() >= df["ds"].iloc[30], method  # nothing before the spike
        assert sig.min() <= df["ds"].iloc[32], method


def test_no_signal_on_stationary_noise_mostly():
    df = make_weekly(200, "noise", seed=3)
    res = alerts.detect(df, "ears_c2")
    assert res["alarm"].mean() < 0.05


def test_missing_values_are_not_scored():
    df = _flat_with_spike()
    df.loc[30, "y"] = np.nan
    res = alerts.detect(df, "ears_c2")
    assert np.isnan(res.loc[30, "score"]) and not res.loc[30, "alarm"]


def test_quality_flags_short_gappy_and_zero_series():
    q = quality.check_series(meta(), make_weekly(60))
    assert q["grade"] == "C" and any("length" in r for r in q["reasons"])
    df = make_weekly(52 * 5)
    df = df.drop(index=range(100, 106))  # 6-week gap
    q = quality.check_series(meta(), df)
    assert q["n_missing"] == 6 and any("gaps" in r for r in q["reasons"])
    g, n = quality.model_input(make_weekly(52 * 4).drop(index=[50, 51]))
    assert n == 2 and g["y"].notna().all()  # short gaps interpolated for model input only
    assert quality.check_series(meta(), make_weekly(52 * 5, "sparse"))["grade"] == "C"
