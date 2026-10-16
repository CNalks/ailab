"""analyse(): quality -> backtest -> policy -> (forecast) -> alerts for one stored series."""

from __future__ import annotations

import pandas as pd

from . import __version__, alerts, gate, quality
from .forecast import (
    BASELINE,
    LEVELS,
    build_forecast,
    choose_model,
    inflate,
    interval_widths,
    rolling_origin_backtest,
    windows_for_seasons,
)
from .policy import apply_gate, decide, rule_from_thresholds
from .schema import AlertRule, SeriesMeta
from .store import SeriesStore
from .transforms import for_kind

SCHEMA_VERSION = (
    "1"  # additive changes only (0.3 adds `gate`, policy.gate_decision/reason_codes/limited)
)
DEFAULT_ALERTS = ("ears_c2", "cusum")
SEASONAL_ALERT = "farrington"  # added when applicable (>= 3 seasons, count/rate kinds)
DEFAULT_STEP = 3
DEFAULT_BACKTEST_SEASONS = 3.0  # default backtest spans >= 3 seasons (n_windows derived from it)
# Cost of the rolling-origin backtest grows roughly quadratically with history length (measured:
# 520 weeks 3.6 s, 1040 weeks 13 s, 1560 weeks 29 s, 5200 weeks > 4 min). Models therefore only see
# the most recent MAX_MODEL_WEEKS (~16 years); quality checks, alerts and exports use all data.
MAX_MODEL_WEEKS = 840


def analyse(
    store: SeriesStore,
    series_id: str,
    horizon: int = 8,
    n_windows: int | None = None,
    step: int = DEFAULT_STEP,
    include_observations: bool = True,
    today: pd.Timestamp | None = None,
) -> dict:
    meta = store.get_meta(series_id)
    raw = store.get_observations(series_id)
    return analyse_frame(
        meta,
        raw,
        store.list_rules(series_id),
        horizon,
        n_windows,
        step,
        include_observations,
        today,
    )


def analyse_frame(
    meta: SeriesMeta,
    raw: pd.DataFrame,
    rules: list[AlertRule] | None = None,
    horizon: int = 8,
    n_windows: int | None = None,
    step: int = DEFAULT_STEP,
    include_observations: bool = True,
    today: pd.Timestamp | None = None,
) -> dict:
    cfg, cfg_src = gate.resolve_thresholds(meta.indicator_type, "W", meta.series_id, meta.gate)
    q = quality.check_series(meta, raw, today, cfg)
    g, n_interp = quality.model_input(raw)
    tf = for_kind(meta.indicator_type, meta.value_scale)
    trim = cfg.reporting_lag_weeks
    g_fit, masked, finfo = quality.model_frame(raw, meta, trim, cfg.max_gap)
    truncated = len(g_fit) > MAX_MODEL_WEEKS
    if truncated:
        g_model = g_fit.tail(MAX_MODEL_WEEKS).reset_index(drop=True)
        masked = masked[-MAX_MODEL_WEEKS:]
    else:
        g_model = g_fit
    if n_windows is None:
        n_windows = windows_for_seasons(DEFAULT_BACKTEST_SEASONS, meta.season_length, step)
    # ---- stage 1: data checks (no model fitting) ---------------------------------------------
    grid = quality.regularise(raw)
    dec = gate.assess_data(meta, grid, g_model, today, cfg, cfg_src, masked)
    lim1 = dec.limits
    bt = None
    h_req = horizon + (finfo["trimmed_provisional"] or 0)
    if not dec.refused and meta.indicator_type != "event":
        bt = rolling_origin_backtest(
            g_model, meta.season_length, h_req, n_windows, step, tf, score_mask=masked
        )
    widths = calib = None
    force = None
    if bt is not None:
        if lim1["baseline_only"]:
            force = min(("Naive", BASELINE), key=lambda m: bt.metrics[m]["mae"])
            model_for_widths = force
        else:
            model_for_widths = choose_model(bt)[0]
        widths = interval_widths(bt, model_for_widths)
        calib = widths["calibration"]
    # ---- stage 2: backtest checks + legacy decision rules ------------------------------------
    chosen, h_ok = (None, 0) if bt is None else choose_model(bt)
    dec = gate.assess_backtest(dec, bt, chosen, h_ok, calib, baseline_only=force is not None)
    policy, model = decide(
        meta.indicator_type,
        q,
        bt,
        calib,
        h_req,
        rule=rule_from_thresholds(cfg),
        force_model=force,
    )
    policy, model = apply_gate(policy, model, dec, finfo["trimmed_provisional"])
    fc = None
    if policy.forecast_allowed and model and widths:
        n_pts = policy.horizon_weeks + finfo["trimmed_provisional"]
        widths = inflate(widths, dec.limits["interval_inflation"])
        fc = build_forecast(
            g_model,
            model,
            widths,
            n_pts,
            meta.season_length,
            tf,
            band=policy.display_mode == "forecast_band",
        )
        fc["calibration"] = {
            k: {**v, "n_windows": widths["n_windows"]} for k, v in widths["calibration"].items()
        }
        fc["finite_sample_valid"] = widths["finite_sample_valid"]
        if widths.get("inflation"):
            fc["interval_inflation"] = widths["inflation"]
        if finfo["trimmed_provisional"]:
            fc["provisional_weeks_in_origin_gap"] = finfo["trimmed_provisional"]
    if truncated:
        policy.warnings.append(
            f"models were fitted on the most recent {MAX_MODEL_WEEKS} weeks only "
            f"({len(g)} available); older history is not used for forecasting"
        )
    if masked.any():
        policy.warnings.append(
            f"{int(masked.sum())} masked weeks were replaced by same-week values of other "
            "seasons for fitting and are excluded from backtest scoring"
        )
    default_methods = list(DEFAULT_ALERTS)
    if meta.indicator_type in ("count", "rate") and len(g) >= (
        cfg.farrington_min_seasons * meta.season_length
    ):
        default_methods.append(SEASONAL_ALERT)
    rule_list = rules or [
        AlertRule(rule_id=f"default_{m}", series_id=meta.series_id, method=m)
        for m in default_methods
    ]
    alert_out = []
    for rule in rule_list:
        if not rule.enabled:
            continue
        alert_out.append(
            {
                "rule_id": rule.rule_id,
                "method": rule.method,
                "baseline_weeks": rule.baseline,
                **alerts.scan(meta, raw, rule.method, rule.baseline, rule.threshold),
            }
        )
    result = {
        "schema_version": SCHEMA_VERSION,
        "ailab_version": __version__,
        "series": meta.model_dump(),
        "data_through": q["data_through"],
        "quality": q,
        "model_input": {
            "interpolated_points": n_interp,
            "max_gap_weeks": cfg.max_gap,
            "model_weeks_used": len(g_model),
            "model_weeks_cap": MAX_MODEL_WEEKS,
            "masked_weeks": int(masked.sum()),
            "provisional_weeks_excluded": finfo["trimmed_provisional"],
            "trailing_missing_trimmed": finfo["trimmed_trailing"],
        },
        "gate": dec.to_dict(),
        "policy": policy.model_dump(),
        "backtest": None if bt is None else _backtest_json(bt, model, widths),
        "forecast": fc,
        "alerts": alert_out,
    }
    if include_observations:
        result["observations"] = [
            {"ds": d.date().isoformat(), "y": None if pd.isna(v) else round(float(v), 5)}
            for d, v in zip(g["ds"], raw_aligned(raw), strict=True)
        ]
    return result


def gate_data_only(
    meta: SeriesMeta, raw: pd.DataFrame, as_of: pd.Timestamp | None = None
) -> gate.GateDecision:
    """Stage-1 gate (data checks only; no model fitting, ~0.1-1 s)."""
    cfg, src = gate.resolve_thresholds(meta.indicator_type, "W", meta.series_id, meta.gate)
    g_fit, masked, _ = quality.model_frame(raw, meta, cfg.reporting_lag_weeks, cfg.max_gap)
    g_model = g_fit.tail(MAX_MODEL_WEEKS).reset_index(drop=True)
    return gate.assess_data(
        meta, quality.regularise(raw), g_model, as_of, cfg, src, masked[-MAX_MODEL_WEEKS:]
    )


def raw_aligned(raw: pd.DataFrame):
    """Observed values on the regular grid (interpolated points are *not* shown as observed)."""
    return quality.regularise(raw)["y"].to_numpy()


def _backtest_json(bt, model: str | None, widths: dict | None) -> dict:
    cv = bt.cv
    return {
        "design": "rolling-origin; each origin refits using only earlier data",
        "n_windows": bt.n_windows,
        "seasons_covered": bt.seasons_covered,
        "transform": bt.transform.name,
        "horizon": bt.horizon,
        "step_weeks": bt.step,
        "first_origin": cv["cutoff"].min().date().isoformat(),
        "last_origin": cv["cutoff"].max().date().isoformat(),
        "mase_scale": round(bt.scale, 6),
        "baseline": "SeasonalNaive",
        "chosen_model": model,
        "models": {
            m: {
                "mae": round(v["mae"], 5),
                "mase": None if v["mase"] is None else round(v["mase"], 4),
                "rel_mae_vs_seasonal_naive": None
                if v["rel_mae_vs_seasonal_naive"] is None
                else round(v["rel_mae_vs_seasonal_naive"], 4),
                "mae_by_horizon": {str(h): round(x, 5) for h, x in v["mae_by_horizon"].items()},
            }
            for m, v in bt.metrics.items()
        },
        "interval_levels": [int(x * 100) for x in LEVELS],
    }
