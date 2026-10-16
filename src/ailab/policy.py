"""Forecast policy: decides whether a forecast may be shown at all, and how.

Rules (first match wins):
1. ``event`` indicators (sparse notifiable events)  -> monitor only
2. data-quality failures (< 3 seasons, long gaps, mostly zeros, negatives) -> monitor/history only
3. backtest impossible                              -> monitor only
4. best candidate does not beat the seasonal-naive baseline at horizon 1 -> monitor only
5. kind-specific extra requirements (``KIND_RULES``; proportions are stricter, see below)
6. otherwise forecast allowed for the leading horizons where it beats the baseline;
   band shown only if leave-one-out 80% coverage >= ``min_band_coverage``.

Proportions are only forecast if they *pass the backtest against seasonal naive* with margin:
overall MAE at most 95 % of the baseline's, a lead of at least 2 horizons, a backtest that spans
at least 3 seasons, and a well-calibrated 80 % band (>= 70 % leave-one-out). Counts/rates need the
basic rules only (warnings are added instead of denials).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import gate as gate_mod
from .forecast import FAMILY, Backtest, choose_model
from .schema import ForecastPolicy, IndicatorType

MIN_BAND_COVERAGE = 0.6
MIN_BACKTEST_SEASONS = 3.0


@dataclass(frozen=True)
class KindRule:
    min_horizons_ok: int  # leading horizons the model must beat the baseline at
    max_rel_mae: float  # overall MAE / baseline MAE must be below this
    min_band_coverage: float  # 80 % leave-one-out coverage required for the band
    deny_on_low_coverage: bool  # deny (True) or withhold only the band (False)
    deny_on_short_backtest: bool  # backtest spanning < MIN_BACKTEST_SEASONS: deny or warn


KIND_RULES: dict[str, KindRule] = {
    "count": KindRule(1, 1.0, MIN_BAND_COVERAGE, False, False),
    "rate": KindRule(1, 1.0, MIN_BAND_COVERAGE, False, False),
    "proportion": KindRule(2, 0.95, 0.70, True, True),
}


def rule_from_thresholds(th: gate_mod.Thresholds) -> KindRule:
    """The legacy per-kind rule expressed with the (possibly overridden) gate thresholds."""
    deny_cov = th.cov80_block > 0
    return KindRule(
        th.min_horizons_ok,
        th.max_rel_mae,
        th.cov80_block if deny_cov else th.cov80_limit,
        deny_cov,
        th.backtest_seasons_block > 0,
    )


def decide(
    indicator_type: IndicatorType,
    quality: dict,
    bt: Backtest | None,
    calibration: dict | None,
    requested_horizon: int,
    proportion_forecast: bool = True,
    rule: KindRule | None = None,
    force_model: str | None = None,
) -> tuple[ForecastPolicy, str | None]:
    """Returns (policy, chosen_model_name_or_None).

    ``rule`` replaces the kind's default rule; ``force_model`` (baseline-only mode) skips the
    skill-vs-baseline checks because no skill is claimed for a baseline model.
    """

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
    rule = rule or KIND_RULES.get(indicator_type, KIND_RULES["count"])
    warnings: list[str] = []
    if force_model is not None:
        model, h_ok = force_model, requested_horizon
        warnings.append(
            "baseline-only forecast (limited mode): no skill over the seasonal-naive baseline is claimed"
        )
    else:
        model, h_ok = choose_model(bt)
        if h_ok == 0:
            return deny("no_skill_vs_seasonal_naive")
        rel = bt.metrics[model]["rel_mae_vs_seasonal_naive"]
        if rel is not None and rel >= rule.max_rel_mae:
            return deny("no_skill_vs_seasonal_naive")
        if h_ok < rule.min_horizons_ok:
            return deny("skill_horizon_too_short")
    cov80 = (calibration or {}).get("80", {}).get("empirical_loo")
    if bt.seasons_covered < MIN_BACKTEST_SEASONS:
        if rule.deny_on_short_backtest:
            return deny("backtest_lt_3_seasons")
        warnings.append(
            f"backtest spans only {bt.seasons_covered:.1f} seasons (< {MIN_BACKTEST_SEASONS:.0f}); "
            "skill and interval calibration are less reliable"
        )
    horizon = min(h_ok, requested_horizon)
    mode = "forecast_band"
    if cov80 is not None and cov80 < rule.min_band_coverage:
        if rule.deny_on_low_coverage:
            return deny("band_undercoverage_in_backtest")
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


def apply_gate(
    policy: ForecastPolicy, model: str | None, gate: gate_mod.GateDecision, trim_weeks: int = 0
) -> tuple[ForecastPolicy, str | None]:
    """Overlay the gate on the (legacy-rule) decision.

    * gate refused, legacy allowed (a check the legacy rules do not know) -> deny with the gate's
      first blocking reason; legacy denial keeps its (historic) ``reason``;
    * gate limited -> constrain horizon / band as the gate says; warnings carry the reasons.
    ``trim_weeks``: provisional weeks excluded from fitting; the forecast starts that many weeks
    before ``data_through`` so ``horizon_weeks`` counts only weeks after it.
    """
    codes = [
        r.code for r in sorted(gate.reasons, key=lambda r: gate_mod.SEVERITY_ORDER[r.severity])
    ]
    upd = {"gate_decision": gate.decision, "reason_codes": codes, "limited": False}
    if gate.refused:
        blk = gate.first_block
        # the backtest is skipped when a data check refuses: "backtest_not_possible" is then only
        # an artefact, the gate's own (data) reason is the real one
        artefact = policy.reason == "backtest_not_possible" and blk.code != "insufficient_backtest"
        if policy.forecast_allowed or artefact:
            reason, mode = gate_mod.LEGACY_REASON.get(blk.code, (blk.code, "monitor_only"))
            return policy.model_copy(
                update={
                    **upd,
                    "forecast_allowed": False,
                    "reason": reason,
                    "display_mode": mode,
                    "model_family": "none",
                    "horizon_weeks": 0,
                }
            ), None
        return policy.model_copy(update=upd), None
    if not policy.forecast_allowed:  # legacy denied but the gate did not: keep the denial visible
        return policy.model_copy(update={**upd, "gate_decision": "refused"}), None
    lim = gate.limits
    warnings = list(policy.warnings)
    horizon = policy.horizon_weeks
    if lim["max_horizon_weeks"]:
        capped = min(horizon, lim["max_horizon_weeks"] + trim_weeks)
        if capped < horizon:
            warnings.append(f"limited mode: horizon capped at {lim['max_horizon_weeks']} weeks")
        horizon = capped
    mode = policy.display_mode
    if lim["point_only"] and mode == "forecast_band":
        mode = "forecast_point_only"
    if lim["interval_inflation"] > 1.0:
        warnings.append(f"limited mode: intervals widened x{lim['interval_inflation']:g}")
    for r in sorted(gate.reasons, key=lambda r: gate_mod.SEVERITY_ORDER[r.severity]):
        if r.severity in ("limit", "warn"):
            warnings.append(f"[{r.code}] " + r.to_dict()["message"]["en"])
    policy = policy.model_copy(
        update={
            **upd,
            "limited": gate.decision == "limited",
            "display_mode": mode,
            "horizon_weeks": max(0, horizon - trim_weeks) if trim_weeks else horizon,
            "warnings": warnings,
        }
    )
    return policy, model
