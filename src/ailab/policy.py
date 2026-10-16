"""Forecast policy: decides whether a forecast may be shown at all, and how.

Rules (first match wins):
1. ``event`` indicators (sparse notifiable events)  -> monitor only
2. data-quality failures (< 3 seasons, long gaps, mostly zeros, negatives) -> monitor/history only
3. backtest impossible                              -> monitor only
4. best candidate does not beat the seasonal-naive baseline at horizon 1 -> monitor only
5. otherwise forecast allowed for the leading horizons where it beats the baseline;
   band shown only if leave-one-out 80% coverage >= ``min_band_coverage``.
"""

from __future__ import annotations

from .forecast import FAMILY, Backtest, choose_model
from .schema import ForecastPolicy, IndicatorType

MIN_BAND_COVERAGE = 0.6


def decide(
    indicator_type: IndicatorType,
    quality: dict,
    bt: Backtest | None,
    calibration: dict | None,
    requested_horizon: int,
    proportion_forecast: bool = True,
) -> tuple[ForecastPolicy, str | None]:
    """Returns (policy, chosen_model_name_or_None)."""

    def deny(reason: str, mode: str = "monitor_only") -> tuple[ForecastPolicy, None]:
        return ForecastPolicy(
            forecast_allowed=False,
            reason=reason,
            display_mode=mode,
            model_family="none",
            horizon_weeks=0,
            indicator_type=indicator_type,
        ), None

    if indicator_type == "event":
        return deny("indicator_type_event_sparse")
    if indicator_type == "proportion" and not proportion_forecast:
        return deny("indicator_type_proportion_monitor_only")
    failed = {c["check"] for c in quality["checks"] if not c["ok"] and c["severity"] == "fail"}
    if "length" in failed:
        return deny("history_lt_3_seasons")
    if "non_zero_share" in failed:
        return deny("sparse_series")
    if "gaps" in failed:
        return deny("data_gaps_too_long", "history_only")
    if "negative_values" in failed:
        return deny("negative_values", "history_only")
    if bt is None:
        return deny("backtest_not_possible")
    model, h_ok = choose_model(bt)
    if h_ok == 0:
        return deny("no_skill_vs_seasonal_naive")
    horizon = min(h_ok, requested_horizon)
    warnings: list[str] = []
    cov80 = (calibration or {}).get("80", {}).get("empirical_loo")
    mode = "forecast_band"
    if cov80 is not None and cov80 < MIN_BAND_COVERAGE:
        mode = "forecast_point_only"
        warnings.append(f"80% band under-covers in backtest ({cov80:.0%}); band withheld")
    if h_ok < requested_horizon:
        warnings.append(
            f"model beats the seasonal-naive baseline only up to {h_ok} weeks; horizon truncated"
        )
    if quality["grade"] == "B":
        warnings.append("data quality grade B: " + "; ".join(quality["reasons"]))
    return ForecastPolicy(
        forecast_allowed=True,
        reason="beats_seasonal_naive_in_rolling_origin_backtest",
        display_mode=mode,
        model_family=FAMILY[model],
        horizon_weeks=horizon,
        indicator_type=indicator_type,
        warnings=warnings,
    ), model
