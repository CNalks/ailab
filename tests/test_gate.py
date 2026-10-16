"""Quality gate: every reason code is triggered by a crafted series; good seasonal series (synthetic
and the real FluView fixture) are not wrongly refused; config layering; i18n; limited mode."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ailab import gate, gate_messages
from ailab.gate import (
    GateConfigError,
    Reason,
    Thresholds,
    assess_alert,
    assess_backtest,
    resolve_thresholds,
)
from ailab.pipeline import analyse_frame, gate_data_only
from ailab.schema import SeriesMeta
from ailab.synth import synth_series

from .conftest import count_series, meta

W52 = 52 * 8


def m52(kind="count", **kw) -> SeriesMeta:
    return SeriesMeta(series_id="g", name="g", indicator_type=kind, season_length=52, **kw)


def stage1(df, m=None, as_of=None) -> dict:
    return gate_data_only(m or m52(), df, as_of).to_dict()


def codes(d: dict, severity: str | None = None) -> set[str]:
    return {r["code"] for r in d["reasons"] if severity is None or r["severity"] == severity}


def seasonal52(seed=0, **kw) -> pd.DataFrame:
    return synth_series(W52, "count", seed, **kw)


# ---------------------------------------------------------------------------------------------
# every reason code, triggered by a crafted series
# ---------------------------------------------------------------------------------------------
def test_event_kind_refused():
    d = stage1(seasonal52(), m52("event"))
    assert d["decision"] == "refused" and "kind_event_not_forecast" in codes(d, "block")
    assert d["forecast_allowed"] is False and d["limits"]["max_horizon_weeks"] is None


def test_too_short_blocks_and_short_history_warns():
    assert "too_short" in codes(stage1(synth_series(52 * 2, "count", 0)), "block")
    d = stage1(synth_series(int(52 * 3.5), "count", 0))
    assert "too_short" not in codes(d) and "short_history" in codes(d, "warn")
    assert d["decision"] == "allowed"


def test_gaps_block_limit_and_trailing():
    df = seasonal52()
    internal = df.copy()
    internal.loc[100:103, "y"] = np.nan  # 4-week hole
    assert "too_many_gaps" in codes(stage1(internal), "block")
    scattered = df.copy()
    scattered.loc[np.arange(5, W52, 8), "y"] = np.nan  # 12.5 % isolated gaps
    d = stage1(scattered)
    assert "too_many_gaps" in codes(d, "limit") and d["decision"] == "limited"
    assert d["limits"]["max_horizon_weeks"] == 4 and d["limits"]["interval_inflation"] > 1
    heavy = df.copy()
    heavy.loc[np.arange(3, W52, 3), "y"] = np.nan  # 33 %
    assert "too_many_gaps" in codes(stage1(heavy), "block")
    tail = df.copy()
    tail.loc[W52 - 3 :, "y"] = np.nan
    assert "long_gap_at_end" in codes(stage1(tail), "block")
    tail1 = df.copy()
    tail1.loc[W52 - 1 :, "y"] = np.nan
    d = stage1(tail1)
    assert "long_gap_at_end" in codes(d, "warn") and d["decision"] == "allowed"


def test_stale_data_only_with_as_of():
    df = seasonal52()
    last = df["ds"].iloc[-1]
    assert "stale_data" not in codes(stage1(df))  # historical data are not judged
    assert "stale_data" in codes(stage1(df, as_of=last + pd.Timedelta(weeks=8)), "warn")
    assert "stale_data" in codes(stage1(df, as_of=last + pd.Timedelta(weeks=40)), "block")
    assert "stale_data" not in codes(stage1(df, as_of=last + pd.Timedelta(weeks=2)))


def test_constant_and_zero_series():
    df = seasonal52()
    const = df.assign(y=7.0)
    assert "constant_series" in codes(stage1(const), "block")
    zeros = df.assign(y=0.0)
    d = stage1(zeros)
    assert {"constant_series", "mostly_zeros"} <= codes(d, "block")


def test_mostly_zeros_warn_and_zero_runs():
    df = seasonal52()
    sparse = df.copy()
    sparse.loc[np.arange(W52) % 10 < 7, "y"] = 0  # 70 % zeros
    d = stage1(sparse)
    assert "mostly_zeros" in codes(d, "block") and d["metrics"]["adi"] > 1
    many = df.copy()
    many.loc[np.arange(W52) % 4 == 0, "y"] = 0  # 25 % zeros
    assert "mostly_zeros_warn" in codes(stage1(many), "warn")
    run = df.copy()
    run.loc[30:41, "y"] = 0  # 12 exact zeros in an active series (implicit zero fill?)
    assert "zero_runs" in codes(stage1(run), "warn")


def test_negative_values_block_on_raw_frames():
    df = seasonal52()
    df.loc[10, "y"] = -3.0
    assert "negative_values" in codes(stage1(df), "block")


def test_level_shift_recent_limits_old_warns():
    df = seasonal52(1)
    recent = df.copy()
    recent.loc[W52 - 60 :, "y"] *= 3.0
    d = stage1(recent)
    assert "structural_break" in codes(d, "limit") and d["decision"] == "limited"
    assert d["limits"]["baseline_only"] is True
    old = df.copy()
    old.loc[100:, "y"] *= 0.3
    d = stage1(old)
    assert "structural_break" in codes(d, "warn") and d["decision"] == "allowed"
    # a smooth trend is not a break
    trend = seasonal52(1, trend=2.0)
    assert "structural_break" not in codes(stage1(trend))


def test_unstable_variance():
    df = seasonal52(2)
    rng = np.random.default_rng(0)
    y = df["y"].to_numpy().copy()
    y[-60:] = rng.poisson(np.maximum(y[-60:], 1) * rng.choice([0.2, 4.0], 60))
    d = stage1(df.assign(y=y))
    assert "unstable_variance" in codes(d)
    assert gate_messages.render(
        gate_messages.REASONS, "unstable_variance", {"var_ratio": 6.0, "levene_p": 0.001}
    )["zh"]


def test_nonstationary_trend_is_only_a_warning():
    rng = np.random.default_rng(1)
    rw = np.round(np.exp(np.cumsum(rng.normal(0.003, 0.12, W52)) + 4.5))
    df = pd.DataFrame({"ds": pd.date_range("2012-01-01", periods=W52, freq="7D"), "y": rw})
    d = stage1(df)
    assert "trend_nonstationary" in codes(d, "warn")
    assert d["hard_to_judge"] is True and d["decision"] == "allowed"
    assert d["recommended_action"]["code"] in ("interpret_with_care", "review_manually")
    assert d["metrics"]["stationarity"]["class"] in ("unit_root_like", "trend_or_break")


def test_no_seasonal_signal_but_seasonal_expected():
    flat = synth_series(W52, "count", 0, amplitude=0.0, dispersion=0.02)
    d = stage1(flat)
    assert "no_seasonal_signal_but_seasonal_expected" in codes(d, "warn")
    assert d["metrics"]["seasonal_strength"] < 0.35
    # not expected when the caller says so
    ok = stage1(flat, m52(gate={"seasonal_expected": "false"}))
    assert "no_seasonal_signal_but_seasonal_expected" not in codes(ok)


def test_outlier_contamination_and_mask_option():
    df = seasonal52(3)
    rng = np.random.default_rng(3)
    idx = rng.choice(np.arange(10, W52 - 10), 24, replace=False)
    y = df["y"].to_numpy().copy()
    y[idx] *= rng.choice([6.0, 0.03], 24)
    d = stage1(df.assign(y=np.round(y)))
    assert "outlier_contamination" in codes(d) and d["metrics"]["outliers"]["n"] >= 10
    assert d["metrics"]["outliers"]["dates"]


def test_reporting_delay_tail_explicit_and_detected():
    df = seasonal52(4)
    lag = stage1(df, m52(gate={"reporting_lag_weeks": 2}))
    r = [x for x in lag["reasons"] if x["code"] == "reporting_delay_tail_incomplete"][0]
    assert r["severity"] == "limit" and lag["limits"]["trim_tail_weeks"] == 2
    cut = df.copy()
    cut.loc[W52 - 2 :, "y"] = (cut.loc[W52 - 2 :, "y"] * 0.03).round()
    d = stage1(cut)
    assert "reporting_delay_tail_incomplete" in codes(d, "warn")
    assert "reporting_delay_tail_incomplete" not in codes(stage1(df))


class _BT:
    """Stub backtest (same shape as in test_forecast_policy)."""

    def __init__(self, rel=0.7, windows=20, seasons=3.5, horizon=8):
        self.n_windows, self.seasons_covered, self.horizon = windows, seasons, horizon
        self.metrics = {
            m: {"mae": 1.0, "rel_mae_vs_seasonal_naive": rel if m != "SeasonalNaive" else 1.0}
            for m in ("SeasonalNaive", "Naive", "MSTL", "AutoTheta")
        }


def _stage2(kind="count", bt=None, chosen="MSTL", h_ok=8, cov80=0.85, base=None, **cfg):
    th, _ = resolve_thresholds(kind, series_overrides=cfg or None)
    dec = base or gate.GateDecision(thresholds=th, kind=kind)
    dec.thresholds = th
    return assess_backtest(dec, bt, chosen, h_ok, {"80": {"empirical_loo": cov80}})


def test_backtest_codes():
    d = _stage2(bt=None)
    assert "insufficient_backtest" in codes(d.to_dict(), "block")
    assert "insufficient_backtest" in codes(_stage2(bt=_BT(windows=7)).to_dict(), "limit")
    assert "insufficient_backtest" in codes(_stage2(bt=_BT(windows=3)).to_dict(), "block")
    d = _stage2(bt=_BT(rel=1.05)).to_dict()
    assert "backtest_worse_than_baseline" in codes(d, "block") and d["forecast_allowed"] is False
    assert "backtest_worse_than_baseline" in codes(_stage2(bt=_BT(), h_ok=0).to_dict(), "block")
    d = _stage2(bt=_BT(rel=0.97)).to_dict()
    assert "marginal_skill" in codes(d, "limit") and d["limited"] is True
    d = _stage2("proportion", bt=_BT(rel=0.97)).to_dict()  # proportions: stricter, refused
    assert "backtest_worse_than_baseline" in codes(d, "block")
    assert "skill_horizon_too_short" in codes(
        _stage2("proportion", bt=_BT(), h_ok=1).to_dict(), "block"
    )
    d = _stage2(bt=_BT(), cov80=0.5).to_dict()
    assert "interval_undercoverage" in codes(d, "limit") and d["limits"]["point_only"] is True
    assert "interval_undercoverage" in codes(
        _stage2("proportion", bt=_BT(), cov80=0.65).to_dict(), "block"
    )
    assert "short_backtest_span" in codes(_stage2(bt=_BT(seasons=1.5)).to_dict(), "warn")
    assert "insufficient_backtest" in codes(
        _stage2("proportion", bt=_BT(seasons=1.5)).to_dict(), "block"
    )
    assert "no_skill_beyond_persistence" in codes(
        _stage2(bt=_BT(), chosen="Naive").to_dict(), "warn"
    )


def test_persistence_with_nonstationarity_is_limited_and_hard_to_judge():
    th, _ = resolve_thresholds("count")
    base = gate.GateDecision(thresholds=th, kind="count")
    base.add(Reason("trend_nonstationary", "warn", {"adf_p": 0.6, "kpss_p": 0.01, "drift": 1.2}))
    d = assess_backtest(base, _BT(), "Naive", 8, {"80": {"empirical_loo": 0.85}}).to_dict()
    assert d["decision"] == "limited" and d["hard_to_judge"] and d["confidence"] == "low"


def test_every_documented_reason_code_has_messages_and_an_action():
    expected = set(gate_messages.REASONS) - {
        c for c in gate_messages.REASONS if c.startswith("alert_")
    }
    expected -= {"gate_check_skipped"}
    for code in expected | {c for c in gate_messages.REASONS if c.startswith("alert_")}:
        for lang in gate_messages.LANGS:
            assert gate_messages.REASONS[code][lang].strip(), (code, lang)
    for code in set(gate.ACTION_FOR.values()):
        for lang in gate_messages.LANGS:
            assert gate_messages.ACTIONS[code][lang].strip(), (code, lang)
    for code in gate.ACTION_FOR:
        assert code in gate_messages.REASONS, code


def test_messages_render_in_three_languages_with_metrics():
    d = stage1(synth_series(52 * 2, "count", 0))
    msg = [r for r in d["reasons"] if r["code"] == "too_short"][0]["message"]
    assert set(msg) == {"en", "zh", "fr"}
    assert "104" in msg["en"] and "104" in msg["zh"] and "104" in msg["fr"]
    assert "季节" in msg["zh"] and "saisons" in msg["fr"]
    assert d["recommended_action"]["code"] == "collect_more_history"
    assert set(d["recommended_action"]["message"]) == {"en", "zh", "fr"}


# ---------------------------------------------------------------------------------------------
# no false rejection of good series
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(6))
def test_good_seasonal_series_pass_stage1_cleanly(seed):
    for kind, df in (
        ("count", seasonal52(seed)),
        ("proportion", synth_series(W52, "proportion", seed)),
    ):
        d = stage1(df, m52(kind))
        assert d["decision"] == "allowed", (kind, seed, d["reason_codes"])
        assert not (codes(d) - {"trend_nonstationary"}), (kind, seed, d["reason_codes"])


def test_seasonal_epidemics_are_not_rejected_for_nonstationarity():
    # a pure seasonal epidemic series with sharp winter peaks: raw ADF/KPSS may disagree, the gate
    # tests the deseasonalised series and never blocks on it
    for seed in range(4):
        d = stage1(seasonal52(seed, sharpness=4.0, amplitude=8.0))
        assert d["forecast_allowed"] and d["decision"] == "allowed", d["reason_codes"]


def test_real_fluview_fixture_is_allowed():
    from ailab.sources import fluview

    raw = fluview.fixture_dataframe()
    wili = fluview.to_series(raw, "wili")
    ili = fluview.to_series(raw, "num_ili")
    d = stage1(wili, m52("proportion", value_scale=100))
    assert d["decision"] == "allowed" and not codes(d, "block") and not codes(d, "limit")
    d = stage1(ili, m52("count"))
    assert d["decision"] == "allowed" and not codes(d, "limit")
    assert codes(d) <= {"trend_nonstationary"}  # participation growth: warning only
    # the COVID seasons are an old break/outlier episode, not a refusal; the mask option exists
    pre = stage1(wili[wili.ds < "2020-03-01"], m52("proportion", value_scale=100))
    assert pre["decision"] == "allowed"


def test_full_pipeline_allows_good_series_and_refuses_noise_consistently():
    res = analyse_frame(meta(), count_series(8), horizon=4, n_windows=14, step=3)
    g = res["gate"]
    assert g["decision"] in ("allowed", "limited") and res["policy"]["forecast_allowed"] is True
    assert res["policy"]["gate_decision"] == g["decision"]
    assert res["policy"]["reason_codes"] == g["reason_codes"]
    assert g["stage"] == "data+backtest" and "backtest" in g["metrics"]


def test_policy_and_gate_never_disagree_on_a_zoo_of_series():
    zoo = [
        (meta(), count_series(8)),
        (meta(), count_series(2)),
        (meta(), count_series(8).assign(y=5.0)),
        (meta(itype="event"), count_series(8)),
        (meta(), count_series(8, level=0.05, amplitude=0)),
        (meta(), count_series(8, missing_frac=0.15)),
        (meta(), count_series(8, missing_frac=0.4)),
        (meta("p", "proportion"), count_series(8).assign(y=lambda d: (d.y / 1000).clip(0, 1))),
    ]
    for m, df in zoo:
        res = analyse_frame(m, df, horizon=4, n_windows=14, step=3)
        allowed = res["policy"]["forecast_allowed"]
        assert allowed == res["gate"]["forecast_allowed"], res["gate"]["reason_codes"]
        assert (res["forecast"] is not None) == allowed
        assert res["gate"]["limited"] == res["policy"]["limited"]
        if not allowed:
            assert res["gate"]["recommended_action"]["code"] != "none"
            assert res["gate"]["reasons"], "a refusal always explains itself"


# ---------------------------------------------------------------------------------------------
# limited mode in the pipeline
# ---------------------------------------------------------------------------------------------
def test_limited_mode_caps_horizon_widens_intervals_and_discloses():
    df = count_series(8, seed=1)
    base = analyse_frame(meta(), df, horizon=8, n_windows=14, step=3)
    lim = analyse_frame(
        meta(),
        df.assign(y=lambda d: d.y.where(np.arange(len(d)) % 8 != 3)),  # 12.5 % isolated gaps
        horizon=8,
        n_windows=14,
        step=3,
    )
    assert lim["gate"]["decision"] == "limited" and lim["policy"]["limited"] is True
    assert lim["policy"]["forecast_allowed"] is True
    assert lim["policy"]["horizon_weeks"] <= 4
    assert base["policy"]["gate_decision"] == "allowed" and base["policy"]["limited"] is False
    assert lim["forecast"]["interval_inflation"] == pytest.approx(1.25)
    assert any("limited mode" in w for w in lim["policy"]["warnings"])
    assert lim["gate"]["confidence"] == "low"


def test_baseline_only_limit_uses_a_baseline_model():
    df = seasonal52(1)
    df.loc[W52 - 60 :, "y"] *= 3.0  # recent level shift => structural_break (limit, baseline-only)
    res = analyse_frame(m52(), df, horizon=4, n_windows=14, step=3)
    g = res["gate"]
    assert "structural_break" in g["reason_codes"] and g["limits"]["baseline_only"] is True
    assert res["policy"]["forecast_allowed"] is True and res["policy"]["limited"] is True
    assert res["policy"]["model_family"] in ("naive", "seasonal_naive")
    assert any("baseline-only" in w for w in res["policy"]["warnings"])
    assert res["forecast"]["interval_inflation"] == pytest.approx(1.5)


def test_reporting_lag_trims_provisional_weeks_from_fit():
    df = count_series(8, seed=5)
    m = meta(gate={"reporting_lag_weeks": 2})
    res = analyse_frame(m, df, horizon=4, n_windows=14, step=3)
    assert res["model_input"]["provisional_weeks_excluded"] == 2
    assert "reporting_delay_tail_incomplete" in res["gate"]["reason_codes"]
    fc = res["forecast"]
    assert fc is not None and fc["provisional_weeks_in_origin_gap"] == 2
    assert (
        fc["origin"] < res["data_through"]
        and len(fc["points"]) == res["policy"]["horizon_weeks"] + 2
    )


def test_mask_periods_replace_fit_values_and_exclude_scoring():
    df = count_series(8, seed=6)
    start, end = df["ds"].iloc[40].date().isoformat(), df["ds"].iloc[49].date().isoformat()
    y = df["y"].to_numpy().copy()
    y[40:50] = 0.0  # disruption
    m = meta(mask_periods=[[start, end]])
    res = analyse_frame(m, df.assign(y=y), horizon=4, n_windows=14, step=3)
    assert res["model_input"]["masked_weeks"] == 10
    assert any("masked weeks" in w for w in res["policy"]["warnings"])
    assert res["policy"]["forecast_allowed"] is True
    # observations are still shown as observed (zeros), never rewritten
    obs = {
        o["ds"]: o["y"]
        for o in analyse_frame(m, df.assign(y=y), horizon=4, n_windows=14, step=3)["observations"]
    }
    assert obs[start] == 0.0


# ---------------------------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------------------------
def test_defaults_per_kind_and_frequency():
    c, _ = resolve_thresholds("count")
    p, src = resolve_thresholds("proportion")
    assert (
        c.max_rel_mae == 1.0 and p.max_rel_mae == 0.95 and src["max_rel_mae"] == "kind:proportion"
    )
    assert p.min_horizons_ok == 2 and p.cov80_block == 0.70 and c.cov80_block == 0.0
    d, _ = resolve_thresholds("count", "D")
    mth, _ = resolve_thresholds("count", "M")
    assert d.min_seasons_block == 8.0 and mth.max_gap == 1 and c.max_gap == 2
    with pytest.raises(GateConfigError):
        resolve_thresholds("count", "H")


def test_layers_env_then_series_override_and_validation(monkeypatch):
    monkeypatch.setenv("AILAB_GATE_MIN_SEASONS_BLOCK", "4")
    th, src = resolve_thresholds("count")
    assert th.min_seasons_block == 4.0 and src["min_seasons_block"] == "env"
    th, src = resolve_thresholds("count", series_overrides={"min_seasons_block": 2})
    assert th.min_seasons_block == 2.0 and src["min_seasons_block"] == "series"
    monkeypatch.setenv(
        "AILAB_GATE_JSON",
        '{"kinds": {"count": {"max_gap": 5}}, "series": {"abc": {"max_gap": 7}}, "all": {"stale_warn_periods": 9}}',
    )
    th, src = resolve_thresholds("count", series_id="abc")
    assert th.max_gap == 7 and src["max_gap"] == "env:series:abc" and th.stale_warn_periods == 9
    th, _ = resolve_thresholds("count", series_id="other")
    assert th.max_gap == 5
    monkeypatch.setenv("AILAB_GATE_JSON", '{"bogus": 1}')
    with pytest.raises(GateConfigError):
        resolve_thresholds("count")
    monkeypatch.setenv("AILAB_GATE_JSON", '{"all": {"nope": 1}}')
    with pytest.raises(GateConfigError, match="unknown gate threshold"):
        resolve_thresholds("count")
    monkeypatch.delenv("AILAB_GATE_JSON")
    monkeypatch.setenv("AILAB_GATE_MAX_GAP", "abc")
    with pytest.raises(GateConfigError, match="invalid value"):
        resolve_thresholds("count")


def test_series_overrides_change_the_decision_and_are_validated():
    df = synth_series(52 * 2 + 30, "count", 0)
    assert "too_short" in codes(stage1(df), "block")
    relaxed = stage1(df, m52(gate={"min_seasons_block": 2.0}))
    assert "too_short" not in codes(relaxed)
    assert relaxed["thresholds"]["min_seasons_block"] == 2.0
    assert relaxed["threshold_sources"]["min_seasons_block"] == "series"
    with pytest.raises(ValueError):
        m52(gate={"min_seasons_blok": 2})
    with pytest.raises(ValueError):
        m52(gate={"max_gap": "x"})
    with pytest.raises(ValueError):
        m52(mask_periods=[["2020-02-01", "2020-01-01"]])


def test_thresholds_dataclass_covers_documented_names():
    names = {f for f in Thresholds.__dataclass_fields__}
    assert {"min_seasons_block", "max_gap", "reporting_lag_weeks", "break_min_shift"} <= names


# ---------------------------------------------------------------------------------------------
# alerts gate
# ---------------------------------------------------------------------------------------------
def _alert(y, kind="count", method="ears_c2", baseline=7, guard=2, sl=52):
    m = SeriesMeta(series_id="a", name="a", indicator_type=kind, season_length=sl)
    return assess_alert(m, np.asarray(y, float), method, baseline, guard).to_dict()


def test_alert_gate_codes():
    rng = np.random.default_rng(0)
    good = rng.poisson(50, 120).astype(float)
    g = _alert(good)
    assert g["status"] == "ok" and g["confidence"] in ("medium", "high")
    assert "alert_baseline_too_short" in g_codes(_alert(good[:20]), "block")
    gappy = good.copy()
    gappy[-9:-2] = np.nan
    assert "alert_baseline_gappy" in g_codes(_alert(gappy), "block")
    zeros = np.zeros(120)
    zeros[::17] = 1
    assert "alert_baseline_low_counts" in g_codes(_alert(zeros), "block")
    assert _alert(zeros, kind="event")["status"] == "degraded"  # sparse events warn, never refuse
    assert "alert_baseline_constant" in g_codes(_alert(np.full(120, 9.0)), "warn")
    spike = good.copy()
    spike[-6] = 600
    assert "alert_baseline_contaminated" in g_codes(_alert(spike), "warn")
    assert "alert_not_enough_seasons" in g_codes(_alert(good, method="farrington"), "block")
    assert "alert_method_not_applicable" in g_codes(
        _alert(rng.uniform(0.1, 0.2, 400), kind="proportion", method="farrington"), "block"
    )


def g_codes(d, sev):
    return {r["code"] for r in d["reasons"] if r["severity"] == sev}
