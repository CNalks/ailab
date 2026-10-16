"""Farrington-style alert + gated alert scans (minimum data, baseline quality, confidence)."""

import numpy as np
import pandas as pd

from ailab import alerts
from ailab.pipeline import analyse_frame
from ailab.schema import SeriesMeta
from ailab.synth import synth_series

from .conftest import count_series, meta


def seasonal(seed=0, n=52 * 6, **kw):
    return synth_series(n, "count", seed, **kw)


def m52(kind="count", **kw):
    return SeriesMeta(series_id="a", name="a", indicator_type=kind, season_length=52, **kw)


def test_farrington_scores_are_roughly_standard_normal_on_clean_seasonal_data():
    df = seasonal(1, 52 * 7)
    r = alerts.detect(df, "farrington", season_length=52)
    s = r["score"].dropna()
    assert len(s) > 150 and s.index.min() >= 52 * 3 - 3  # needs >= 3 reference seasons
    assert abs(s.mean()) < 0.4 and 0.6 < s.std() < 1.4
    assert (s > alerts.DEFAULT_THRESHOLD["farrington"]).mean() < 0.03


def test_farrington_detects_an_injected_outbreak_and_is_quiet_before_it():
    df = seasonal(2, 52 * 6, dispersion=0.02)
    y = df["y"].to_numpy().copy()
    t0 = 52 * 5 + 20  # onset in the off-season
    for w in range(8):
        y[t0 + w] = y[t0 + w] * (1 + 2.0 * np.sin(np.pi * (w + 1) / 9))
    df = df.assign(y=np.round(y))
    far = alerts.detect(df, "farrington", season_length=52)
    assert far.loc[t0 : t0 + 7, "alarm"].any()
    assert far.loc[52 * 4 : t0 - 1, "alarm"].sum() <= 1  # ~1 false alarm / 100 clean weeks


def test_farrington_downweights_past_outbreaks_in_the_reference_window():
    df = seasonal(3, 52 * 6, dispersion=0.02)
    y = df["y"].to_numpy().copy()
    y[52 * 2 + 10 : 52 * 2 + 14] *= 6  # old outbreak, same weeks as the one we test
    t = 52 * 5 + 11
    clean = alerts.detect(df, "farrington", season_length=52)
    dirty = alerts.detect(df.assign(y=y), "farrington", season_length=52)
    # expected value at t is hardly inflated by the old outbreak
    assert dirty.loc[t, "expected"] < 1.25 * clean.loc[t, "expected"]


def test_scan_refuses_short_series_with_reasons_and_suppresses_signals():
    df = seasonal(0, 20)
    df.loc[18, "y"] = df["y"].iloc[:15].max() * 5
    out = alerts.scan(m52(), df, "ears_c2", 7)
    assert out["status"] == "refused" and out["confidence"] == "none"
    assert "alert_baseline_too_short" in out["gate"]["reason_codes"]
    assert out["latest_is_signal"] is False and out["signal_periods"] == []
    assert out["n_signals_suppressed"] >= 0 and out["gate"]["reasons"][0]["message"]["zh"]


def test_scan_ok_has_confidence_and_keeps_signals():
    df = seasonal(0, 52 * 3, amplitude=0.0, dispersion=0.0)
    df.loc[len(df) - 1, "y"] += 400
    out = alerts.scan(m52(), df, "ears_c2", 7)
    assert out["status"] == "ok" and out["confidence"] in ("medium", "high")
    assert out["latest_is_signal"] is True and out["n_signals"] >= 1


def test_scan_refuses_when_latest_baseline_is_mostly_zeros_and_low_counts_are_not_scored():
    y = np.zeros(120)
    y[::19] = 1
    df = pd.DataFrame({"ds": pd.date_range("2020-01-05", periods=120, freq="7D"), "y": y})
    out = alerts.scan(m52(), df, "ears_c2", 7)
    assert out["status"] == "refused" and "alert_baseline_low_counts" in out["gate"]["reason_codes"]
    assert out["n_evaluated"] == 0  # per-week suppression of near-zero baselines


def test_masked_periods_are_not_scored_or_used_in_baselines():
    df = seasonal(0, 52 * 3, amplitude=0.0, dispersion=0.0)
    start, end = df["ds"].iloc[60].date().isoformat(), df["ds"].iloc[70].date().isoformat()
    df.loc[60:70, "y"] = 0
    out = alerts.scan(m52(mask_periods=[[start, end]]), df, "ears_c2", 7, recent=200)
    rec = {r["ds"]: r for r in out["recent"]}
    assert start not in rec  # masked weeks carry no score


def test_pipeline_adds_farrington_for_seasonal_counts_only():
    res = analyse_frame(meta(), count_series(8), horizon=4, n_windows=14, step=3)
    assert [a["method"] for a in res["alerts"]] == ["ears_c2", "cusum", "farrington"]
    for a in res["alerts"]:
        assert a["status"] in ("ok", "degraded", "refused") and "gate" in a
    prop = analyse_frame(
        meta("p", "proportion"),
        count_series(8).assign(y=lambda d: (d.y / 1000).clip(0.001, 1)),
        horizon=4,
        n_windows=14,
        step=3,
    )
    assert [a["method"] for a in prop["alerts"]] == ["ears_c2", "cusum"]


def test_unknown_method_and_defaults():
    import pytest

    with pytest.raises(ValueError):
        alerts.detect(seasonal(0, 100), "nope")
    assert alerts.DEFAULT_THRESHOLD["farrington"] == 2.0
