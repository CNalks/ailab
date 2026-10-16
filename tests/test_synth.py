import numpy as np
import pandas as pd

from ailab.synth import synth_pair, synth_series


def test_deterministic_and_seed_sensitive():
    a = synth_series(100, "count", seed=7)
    b = synth_series(100, "count", seed=7)
    c = synth_series(100, "count", seed=8)
    pd.testing.assert_frame_equal(a, b)
    assert not a["y"].equals(c["y"])


def test_weekly_sunday_grid():
    df = synth_series(30)
    assert (df["ds"].diff().dropna() == pd.Timedelta(days=7)).all()
    assert df["ds"].iloc[0].dayofweek == 6


def test_counts_are_nonnegative_integers_and_seasonal():
    df = synth_series(52 * 5, "count", seed=1, season_length=52)
    y = df["y"].to_numpy()
    assert (y >= 0).all() and np.all(y == np.round(y))
    s = pd.Series(y)
    assert s.autocorr(52) > 0.5  # strong yearly seasonality


def test_missing_weeks_are_nan_not_zero():
    df = synth_series(80, missing=[5, 6], missing_frac=0.1, seed=3)
    assert df["y"].isna().sum() >= 2 and df["y"].isna().iloc[[5, 6]].all()
    assert (df["y"].dropna() >= 0).all()
    df2 = synth_series(80, missing=[5, 6], missing_frac=0.1, seed=3)
    assert df["y"].isna().equals(df2["y"].isna())


def test_outbreak_spike_is_visible():
    base = synth_series(104, amplitude=0.0, dispersion=0, seed=2)
    spiky = synth_series(104, amplitude=0.0, dispersion=0, seed=2, outbreaks=[(60, 5.0, 1.5)])
    assert spiky["y"].iloc[58:63].max() > 3 * base["y"].median()
    assert spiky["y"].idxmax() in range(57, 64)


def test_proportion_bounds_and_scale():
    f = synth_series(200, "proportion", seed=4)
    p = synth_series(200, "proportion", seed=4, value_scale=100)
    assert f["y"].between(0, 1).all() and p["y"].between(0, 100).all()
    assert np.allclose(f["y"] * 100, p["y"])


def test_pair_numerator_never_exceeds_denominator():
    num, den = synth_pair(150, seed=1, outbreaks=[(70, 2.0, 2)], missing_den=[10])
    both = pd.concat([num["y"], den["y"]], axis=1, keys=["n", "d"]).dropna()
    assert (both["n"] <= both["d"]).all() and (both["d"] > 0).all()
    assert np.isnan(den["y"].iloc[10])
