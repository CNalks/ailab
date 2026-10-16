"""analyse(): quality -> backtest -> policy -> (forecast) -> alerts for one stored series."""

from __future__ import annotations

import pandas as pd

from . import __version__, alerts, quality
from .forecast import (
    LEVELS,
    build_forecast,
    choose_model,
    interval_widths,
    rolling_origin_backtest,
    windows_for_seasons,
)
from .policy import decide
from .schema import AlertRule, SeriesMeta
from .store import SeriesStore
from .transforms import for_kind

SCHEMA_VERSION = "1"
DEFAULT_ALERTS = ("ears_c2", "cusum")
DEFAULT_STEP = 3
DEFAULT_BACKTEST_SEASONS = 3.0  # default backtest spans >= 3 seasons (n_windows derived from it)


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
    q = quality.check_series(meta, raw, today)
    g, n_interp = quality.model_input(raw)
    tf = for_kind(meta.indicator_type, meta.value_scale)
    if n_windows is None:
        n_windows = windows_for_seasons(DEFAULT_BACKTEST_SEASONS, meta.season_length, step)
    bt = None
    # backtest only when the quality gate does not already forbid forecasting
    quality_blocks = {c["check"] for c in q["checks"] if not c["ok"] and c["severity"] == "fail"}
    if not quality_blocks and meta.indicator_type != "event":
        bt = rolling_origin_backtest(g, meta.season_length, horizon, n_windows, step, tf)
    widths = calib = None
    if bt is not None:
        widths = interval_widths(bt, choose_model(bt)[0])
        calib = widths["calibration"]
    policy, model = decide(meta.indicator_type, q, bt, calib, horizon)
    fc = None
    if policy.forecast_allowed and model and widths:
        fc = build_forecast(
            g,
            model,
            widths,
            policy.horizon_weeks,
            meta.season_length,
            tf,
            band=policy.display_mode == "forecast_band",
        )
        fc["calibration"] = {
            k: {**v, "n_windows": widths["n_windows"]} for k, v in widths["calibration"].items()
        }
        fc["finite_sample_valid"] = widths["finite_sample_valid"]
    rule_list = rules or [
        AlertRule(rule_id=f"default_{m}", series_id=meta.series_id, method=m)
        for m in DEFAULT_ALERTS
    ]
    alert_out = []
    for rule in rule_list:
        if not rule.enabled:
            continue
        res = alerts.evaluate_rule(rule, g)
        alert_out.append(
            {
                "rule_id": rule.rule_id,
                "method": rule.method,
                "baseline_weeks": rule.baseline,
                **alerts.summarise(res),
            }
        )
    result = {
        "schema_version": SCHEMA_VERSION,
        "ailab_version": __version__,
        "series": meta.model_dump(),
        "data_through": q["data_through"],
        "quality": q,
        "model_input": {"interpolated_points": n_interp, "max_gap_weeks": quality.MAX_INTERP_GAP},
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
