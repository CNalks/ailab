"""Forecast quality gate: decides *before* (data checks) and *after* (backtest checks) fitting
whether a series may be forecast, may be forecast in a limited way, or must be refused.

Design (see docs/MODEL_POLICY.md for the user-facing description):

* Every check yields zero or more **reasons**: machine code, severity (``block`` | ``limit`` |
  ``warn``), the measured metrics, the thresholds used and a message in en / zh / fr.
* decision = ``refused`` if any ``block``; else ``limited`` if any ``limit``; else ``allowed``.
  ``limited`` never hides the forecast, it constrains it (shorter horizon, wider intervals,
  baseline-only model, no band, provisional tail excluded) via ``limits``.
* Stationarity is deliberately **not** a gate: seasonal epidemics and growing counts are
  "non-stationary" by textbook tests. ADF/KPSS on the STL-deseasonalised series only produce a
  warning (and a "hard to judge" flag); the refusal criterion for forecastability is backtest skill
  against the seasonal-naive baseline (stage 2).
* Mature code is reused: STL (seasonal strength, residuals), ADF/KPSS, Levene, HAC OLS
  (statsmodels / scipy). Only glue and small robust heuristics (Hampel outliers, level-shift scan,
  tail-drop) are custom.

Thresholds depend on (kind, frequency) and can be overridden in three layers, later wins:
defaults < environment (``AILAB_GATE_<KEY>`` and ``AILAB_GATE_JSON``) < per-series ``meta.gate``.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import gate_messages as msg
from .schema import SeriesMeta
from .transforms import Transform, for_kind

GATE_VERSION = "1"
SEVERITY_ORDER = {"block": 0, "limit": 1, "warn": 2}

# ---------------------------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------------------------


class GateConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Thresholds:
    """All gate thresholds. Period counts are in the series' own periods (weeks for ``W``)."""

    # --- history length --------------------------------------------------------------------
    min_seasons_block: float = 3.0  # < this many seasons of data: refuse (too_short)
    min_seasons_warn: float = 4.0  # < this: warn (short_history)
    # --- missing data ----------------------------------------------------------------------
    max_gap: int = 2  # longest internal run of missing periods (also the interpolation limit)
    max_trailing_gap: int = 2  # missing periods at the very end: more => refuse
    max_missing_frac_limit: float = 0.10  # above: limited forecast
    max_missing_frac_block: float = 0.25  # above: refuse
    # --- timeliness (only evaluated when an ``as_of`` date is given) -----------------------
    stale_warn_periods: int = 4
    stale_block_periods: int = 26
    # --- degenerate series -----------------------------------------------------------------
    constant_rel_sd: float = 1e-3  # sd / |mean| below this counts as constant
    min_nonzero_block: float = 0.5  # share of observed periods > 0 below this: refuse
    min_nonzero_warn: float = 0.8
    zero_run_warn: int = 8
    # --- seasonality -----------------------------------------------------------------------
    seasonal_expected: str = "auto"  # auto (season_length >= 4) | true | false
    min_seasonal_strength: float = 0.35  # Hyndman STL seasonal strength F_S
    # --- stationarity (warning-level only; see module docstring) ---------------------------
    adf_alpha: float = 0.05
    kpss_alpha: float = 0.05
    drift_warn: float = 0.5  # |last season mean - first season mean| of the deseasonalised series
    # --- structural break / variance ---------------------------------------------------------
    break_min_shift: float = 0.5  # model units (log / logit): ~65 % level change
    break_min_sup_t: float = 10.0  # HAC t of the step dummy (detrended scan)
    break_recent_seasons: float = 1.5  # break inside the last N seasons => limit, else warn
    var_ratio_warn: float = 2.5
    var_ratio_limit: float = 5.0
    # --- outliers ----------------------------------------------------------------------------
    outlier_z: float = 6.0  # Hampel: |dev| / robust scale
    outlier_min_dev: float = 0.5  # and at least this far from the local median (model units)
    outlier_share_warn: float = 0.02
    outlier_share_limit: float = 0.06
    # --- reporting delay -------------------------------------------------------------------
    reporting_lag_weeks: int = 0  # explicit: last N weeks are provisional => excluded from fit
    tail_drop_z: float = 5.0
    tail_min_drop: float = 0.5  # model units below the seasonal expectation
    tail_max_weeks: int = 3
    # --- backtest (stage 2) ------------------------------------------------------------------
    min_windows_block: int = 5
    min_windows_limit: int = 10
    max_rel_mae: float = 1.0  # best candidate MAE / seasonal-naive MAE must be below this
    marginal_rel_mae: float = 0.95  # >= this (but passing): limited
    min_horizons_ok: int = 1  # leading horizons that must beat the baseline
    cov80_block: float = 0.0  # empirical 80 % leave-one-out coverage below this: refuse (0 = off)
    cov80_limit: float = 0.6  # below this: band withheld (limited)
    backtest_seasons_block: float = 0.0  # backtest span below this many seasons: refuse (0 = off)
    backtest_seasons_warn: float = 3.0
    # --- what "limited" means ------------------------------------------------------------------
    limited_max_horizon: int = 4
    limited_interval_inflation: float = 1.25
    break_interval_inflation: float = 1.5
    # --- alerts ------------------------------------------------------------------------------
    alert_min_eval_periods: int = 26  # evaluable periods needed beyond baseline + guard
    alert_min_baseline_nonzero: float = 0.5
    alert_min_baseline_mean: float = 1.0  # mean of the baseline (count/rate kinds)
    alert_min_obs_frac: float = 0.7
    farrington_min_seasons: float = 3.0


_FIELD_TYPES = {f.name: f.type for f in dataclasses.fields(Thresholds)}

# per-kind adjustments (on top of the defaults)
KIND_OVERRIDES: dict[str, dict[str, Any]] = {
    "count": {},
    "rate": {},
    "event": {},
    # proportions are only forecast with margin: MAE <= 95 % of baseline, a lead of >= 2 horizons,
    # a backtest of >= 3 seasons and a well-calibrated 80 % band (the pre-0.3 policy rules)
    "proportion": {
        "max_rel_mae": 0.95,
        "min_horizons_ok": 2,
        "cov80_block": 0.70,
        "cov80_limit": 0.70,
        "backtest_seasons_block": 3.0,
    },
}

# per-frequency adjustments; only ``W`` is reachable through the service today (series are weekly)
# but the gate itself is frequency-agnostic: D/M tables are used by library callers.
FREQ_OVERRIDES: dict[str, dict[str, Any]] = {
    "W": {},
    "M": {
        "max_gap": 1,
        "max_trailing_gap": 1,
        "stale_warn_periods": 2,
        "stale_block_periods": 6,
        "zero_run_warn": 3,
        "limited_max_horizon": 3,
        "tail_max_weeks": 1,
        "alert_min_eval_periods": 12,
        "min_windows_block": 5,
        "min_windows_limit": 8,
    },
    "D": {
        "min_seasons_block": 8.0,  # season = 7 days: 8 weeks
        "min_seasons_warn": 12.0,
        "max_gap": 3,
        "max_trailing_gap": 3,
        "stale_warn_periods": 7,
        "stale_block_periods": 60,
        "zero_run_warn": 14,
        "limited_max_horizon": 7,
        "tail_max_weeks": 3,
        "alert_min_eval_periods": 56,
        "min_windows_limit": 14,
    },
}


def _coerce(name: str, value: Any, source: str) -> Any:
    if name not in _FIELD_TYPES:
        raise GateConfigError(f"unknown gate threshold {name!r} ({source})")
    typ = _FIELD_TYPES[name]
    try:
        if typ in ("int", int):
            f = float(value)
            if f != int(f):
                raise ValueError("not an integer")
            return int(f)
        if typ in ("float", float):
            f = float(value)
            if not math.isfinite(f):
                raise ValueError("not finite")
            return f
        if typ in ("str", str):
            s = str(value).strip().lower()
            if name == "seasonal_expected" and s not in ("auto", "true", "false"):
                raise ValueError("must be auto|true|false")
            return s
    except (TypeError, ValueError) as exc:
        raise GateConfigError(f"gate threshold {name!r}: invalid value {value!r} ({exc})") from None
    raise GateConfigError(f"unsupported type for {name}")


def env_overrides(environ: dict[str, str] | None = None) -> dict[str, dict]:
    """Parse ``AILAB_GATE_*`` variables.

    * ``AILAB_GATE_<THRESHOLD>=value`` e.g. ``AILAB_GATE_MIN_SEASONS_BLOCK=4``: applies to all
      series;
    * ``AILAB_GATE_JSON='{"kinds":{"count":{...}},"series":{"my_id":{...}},"all":{...}}'``.

    Returns ``{"all": {...}, "kinds": {...}, "series": {...}}`` with validated values;
    raises :class:`GateConfigError` for unknown names or malformed values.
    """
    env = os.environ if environ is None else environ
    out: dict[str, dict] = {"all": {}, "kinds": {}, "series": {}}
    raw = env.get("AILAB_GATE_JSON", "").strip()
    if raw:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GateConfigError(f"AILAB_GATE_JSON is not valid JSON: {exc.msg}") from None
        if not isinstance(doc, dict) or set(doc) - {"all", "kinds", "series"}:
            raise GateConfigError("AILAB_GATE_JSON keys must be among: all, kinds, series")
        for k, v in (doc.get("all") or {}).items():
            out["all"][k] = _coerce(k, v, "AILAB_GATE_JSON.all")
        for kind, d in (doc.get("kinds") or {}).items():
            if kind not in KIND_OVERRIDES:
                raise GateConfigError(f"AILAB_GATE_JSON: unknown kind {kind!r}")
            out["kinds"][kind] = {k: _coerce(k, v, f"kinds.{kind}") for k, v in d.items()}
        for sid, d in (doc.get("series") or {}).items():
            out["series"][sid] = {k: _coerce(k, v, f"series.{sid}") for k, v in d.items()}
    for name in _FIELD_TYPES:
        key = f"AILAB_GATE_{name.upper()}"
        if env.get(key, "") != "":
            out["all"][name] = _coerce(name, env[key], key)
    return out


def resolve_thresholds(
    kind: str,
    frequency: str = "W",
    series_id: str | None = None,
    series_overrides: dict | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[Thresholds, dict[str, str]]:
    """Layered thresholds and where each non-default value came from."""
    if frequency not in FREQ_OVERRIDES:
        raise GateConfigError(f"unknown frequency {frequency!r}")
    vals: dict[str, Any] = {}
    src: dict[str, str] = {}
    for layer, d in (
        (f"frequency:{frequency}", FREQ_OVERRIDES[frequency]),
        (f"kind:{kind}", KIND_OVERRIDES.get(kind, {})),
    ):
        for k, v in d.items():
            vals[k], src[k] = v, layer
    env = env_overrides(environ)
    layers = [("env", env["all"]), (f"env:kind:{kind}", env["kinds"].get(kind, {}))]
    if series_id is not None:
        layers.append((f"env:series:{series_id}", env["series"].get(series_id, {})))
    layers.append(
        (
            "series",
            {k: _coerce(k, v, "series.gate") for k, v in (series_overrides or {}).items()},
        )
    )
    for layer, d in layers:
        for k, v in d.items():
            vals[k], src[k] = v, layer
    return Thresholds(**vals), src


def validate_series_overrides(d: dict) -> dict:
    """For ``SeriesMeta.gate``: reject unknown keys / bad values early (API 422)."""
    return {k: _coerce(k, v, "series.gate") for k, v in d.items()}


# ---------------------------------------------------------------------------------------------
# result objects
# ---------------------------------------------------------------------------------------------

# reason code -> (default recommended action, group)
ACTION_FOR = {
    "kind_event_not_forecast": "monitor_only",
    "kind_proportion_disabled": "monitor_only",
    "negative_values": "review_series_definition",
    "too_short": "collect_more_history",
    "short_history": "collect_more_history",
    "too_many_gaps": "repair_gaps",
    "long_gap_at_end": "wait_for_data_update",
    "stale_data": "wait_for_data_update",
    "constant_series": "review_series_definition",
    "mostly_zeros": "monitor_only",
    "mostly_zeros_warn": "monitor_only",
    "zero_runs": "repair_gaps",
    "structural_break": "review_structural_break",
    "unstable_variance": "interpret_with_care",
    "trend_nonstationary": "interpret_with_care",
    "no_seasonal_signal_but_seasonal_expected": "interpret_with_care",
    "outlier_contamination": "mask_outlier_period",
    "reporting_delay_tail_incomplete": "set_reporting_lag",
    "insufficient_backtest": "extend_backtest",
    "short_backtest_span": "collect_more_history",
    "backtest_worse_than_baseline": "monitor_only",
    "marginal_skill": "use_limited_forecast",
    "skill_horizon_too_short": "monitor_only",
    "no_skill_beyond_persistence": "use_limited_forecast",
    "interval_undercoverage": "use_limited_forecast",
    "alert_baseline_too_short": "collect_more_history",
    "alert_baseline_gappy": "repair_gaps",
    "alert_baseline_low_counts": "monitor_only",
    "alert_baseline_constant": "review_series_definition",
    "alert_baseline_contaminated": "mask_outlier_period",
    "alert_not_enough_seasons": "collect_more_history",
    "alert_method_not_applicable": "review_manually",
}
# legacy policy.reason / display_mode for each blocking code
LEGACY_REASON = {
    "kind_event_not_forecast": ("indicator_type_event_sparse", "monitor_only"),
    "kind_proportion_disabled": ("indicator_type_proportion_monitor_only", "monitor_only"),
    "too_short": ("history_lt_3_seasons", "monitor_only"),
    "mostly_zeros": ("sparse_series", "monitor_only"),
    "too_many_gaps": ("data_gaps_too_long", "history_only"),
    "long_gap_at_end": ("data_gaps_too_long", "history_only"),
    "negative_values": ("negative_values", "history_only"),
    "constant_series": ("constant_series", "monitor_only"),
    "stale_data": ("stale_data", "history_only"),
    "insufficient_backtest": ("backtest_not_possible", "monitor_only"),
    "backtest_worse_than_baseline": ("no_skill_vs_seasonal_naive", "monitor_only"),
    "skill_horizon_too_short": ("skill_horizon_too_short", "monitor_only"),
    "interval_undercoverage": ("band_undercoverage_in_backtest", "monitor_only"),
}
# order in which blocking codes are reported as *the* reason (first match wins, like the
# pre-0.3 decision order)
BLOCK_PRIORITY = (
    "kind_event_not_forecast",
    "kind_proportion_disabled",
    "too_short",
    "mostly_zeros",
    "too_many_gaps",
    "long_gap_at_end",
    "negative_values",
    "constant_series",
    "stale_data",
    "insufficient_backtest",
    "backtest_worse_than_baseline",
    "skill_horizon_too_short",
    "interval_undercoverage",
)
HARD_TO_JUDGE_CODES = {"trend_nonstationary", "structural_break", "unstable_variance"}


@dataclass
class Reason:
    code: str
    severity: str  # block | limit | warn
    metrics: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)  # constraints applied when severity == limit
    text_vars: dict = field(default_factory=dict)  # extra placeholders for the messages

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": msg.render(msg.REASONS, self.code, {**self.metrics, **self.text_vars}),
            "metrics": _clean(self.metrics),
            "thresholds": _clean(self.thresholds),
        }


@dataclass
class GateDecision:
    reasons: list[Reason] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    thresholds: Thresholds = field(default_factory=Thresholds)
    threshold_sources: dict = field(default_factory=dict)
    stage: str = "data"
    kind: str = "count"
    frequency: str = "W"
    as_of: str | None = None
    skipped: dict = field(default_factory=dict)
    confidence_override: str | None = None

    # --- derived -------------------------------------------------------------------------
    def has(self, code: str, severity: str | None = None) -> bool:
        return any(
            r.code == code and (severity is None or r.severity == severity) for r in self.reasons
        )

    def add(self, r: Reason | None) -> None:
        if r is not None:
            self.reasons.append(r)

    @property
    def refused(self) -> bool:
        return any(r.severity == "block" for r in self.reasons)

    @property
    def decision(self) -> str:
        if self.refused:
            return "refused"
        return "limited" if any(r.severity == "limit" for r in self.reasons) else "allowed"

    @property
    def first_block(self) -> Reason | None:
        blocks = [r for r in self.reasons if r.severity == "block"]
        if not blocks:
            return None
        return min(
            blocks,
            key=lambda r: BLOCK_PRIORITY.index(r.code) if r.code in BLOCK_PRIORITY else 99,
        )

    @property
    def limits(self) -> dict:
        """Merged constraints of all ``limit`` reasons (strictest wins)."""
        out = {
            "max_horizon_weeks": None,
            "interval_inflation": 1.0,
            "baseline_only": False,
            "point_only": False,
            "trim_tail_weeks": 0,
        }
        if self.refused:
            return out
        for r in self.reasons:
            if r.severity != "limit":
                continue
            lim = r.limits
            if lim.get("max_horizon_weeks"):
                cur = out["max_horizon_weeks"]
                out["max_horizon_weeks"] = (
                    lim["max_horizon_weeks"] if cur is None else min(cur, lim["max_horizon_weeks"])
                )
            out["interval_inflation"] = max(
                out["interval_inflation"], lim.get("interval_inflation", 1.0)
            )
            out["baseline_only"] |= bool(lim.get("baseline_only"))
            out["point_only"] |= bool(lim.get("point_only"))
            out["trim_tail_weeks"] = max(out["trim_tail_weeks"], int(lim.get("trim_tail_weeks", 0)))
        return out

    @property
    def hard_to_judge(self) -> bool:
        return any(r.code in HARD_TO_JUDGE_CODES for r in self.reasons) and (
            self.has("trend_nonstationary")
            or any(r.severity != "warn" for r in self.reasons if r.code in HARD_TO_JUDGE_CODES)
        )

    @property
    def confidence(self) -> str:
        if self.confidence_override:
            return self.confidence_override
        if self.refused:
            return "none"
        if self.decision == "limited":
            return "low"
        if self.reasons:
            return "medium"
        return "high"

    def recommended_action(self) -> dict:
        order = sorted(self.reasons, key=lambda r: SEVERITY_ORDER[r.severity])
        code, vars_ = "none", {}
        if order:
            top = order[0]
            code = ACTION_FOR.get(top.code, "review_manually")
            vars_ = {
                **top.metrics,
                **top.text_vars,
                "min_seasons": self.thresholds.min_seasons_block,
            }
            if top.severity == "warn" and top.code in HARD_TO_JUDGE_CODES:
                code = "interpret_with_care"
            elif self.hard_to_judge and top.severity == "limit":
                code = "review_manually"
        return {"code": code, "message": msg.render(msg.ACTIONS, code, vars_)}

    def to_dict(self) -> dict:
        reasons = sorted(self.reasons, key=lambda r: SEVERITY_ORDER[r.severity])
        dec = self.decision
        return {
            "gate_version": GATE_VERSION,
            "stage": self.stage,
            "decision": dec,
            "forecast_allowed": dec != "refused",
            "limited": dec == "limited",
            "confidence": self.confidence,
            "hard_to_judge": self.hard_to_judge,
            "indicator_type": self.kind,
            "frequency": self.frequency,
            "as_of": self.as_of,
            "reasons": [r.to_dict() for r in reasons],
            "reason_codes": [r.code for r in reasons],
            "limits": self.limits,
            "recommended_action": self.recommended_action(),
            "metrics": _clean(self.metrics),
            "thresholds": dataclasses.asdict(self.thresholds),
            "threshold_sources": dict(self.threshold_sources),
            "skipped_checks": self.skipped,
        }


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_clean(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float):
        return None if not math.isfinite(x) else round(x, 5)
    return x


# ---------------------------------------------------------------------------------------------
# statistics (pure numpy / statsmodels; unit-testable)
# ---------------------------------------------------------------------------------------------


def stl_decompose(z: np.ndarray, period: int):
    """Robust STL on the (transformed, gap-free) series; None if too short."""
    if period < 2 or len(z) < 2 * period + 2:
        return None
    from statsmodels.tsa.seasonal import STL

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return STL(z, period=period, robust=True).fit()


def seasonal_strength(res) -> float:
    """Hyndman & Athanasopoulos: F_S = max(0, 1 - Var(R) / Var(S + R))."""
    v = np.var(res.seasonal + res.resid)
    return 0.0 if v <= 0 else float(max(0.0, 1.0 - np.var(res.resid) / v))


def stationarity(d: np.ndarray, adf_alpha: float, kpss_alpha: float, season_length: int) -> dict:
    """ADF + KPSS on the deseasonalised series (+ ADF on its first difference).

    Classification (Kwiatkowski et al. 1992 joint reading):
    ``stationary`` (ADF rejects unit root, KPSS does not reject stationarity),
    ``unit_root_like`` (ADF does not reject, KPSS rejects),
    ``trend_or_break`` (both reject: e.g. deterministic trend / breaks),
    ``inconclusive`` (neither rejects: low power).
    """
    from statsmodels.tsa.stattools import adfuller, kpss

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        maxlag = int(min(12 * (len(d) / 100) ** 0.25, max(1, len(d) // 4 - 2)))
        adf_p = float(adfuller(d, maxlag=maxlag, autolag="AIC")[1])
        kpss_p = float(kpss(d, regression="c", nlags="auto")[1])  # clipped to [0.01, 0.10]
        adf_diff_p = float(adfuller(np.diff(d), maxlag=maxlag, autolag="AIC")[1])
    adf_rej, kpss_rej = adf_p < adf_alpha, kpss_p < kpss_alpha
    if adf_rej and not kpss_rej:
        cls = "stationary"
    elif not adf_rej and kpss_rej:
        cls = "unit_root_like"
    elif adf_rej and kpss_rej:
        cls = "trend_or_break"
    else:
        cls = "inconclusive"
    sl = max(2, season_length)
    ns = len(d) // sl
    means = [float(np.mean(d[len(d) - (i + 1) * sl : len(d) - i * sl])) for i in range(ns)][::-1]
    drift = means[-1] - means[0] if len(means) >= 2 else 0.0
    return {
        "adf_p": adf_p,
        "kpss_p": kpss_p,
        "adf_diff_p": adf_diff_p,
        "class": cls,
        "drift": float(drift),
        "season_means": [round(m, 4) for m in means],
    }


def variance_stability(resid: np.ndarray, season_length: int) -> dict:
    """Recent-season residual spread vs earlier history (+ Levene across seasons)."""
    from scipy import stats

    sl = max(2, season_length)
    if len(resid) < 2 * sl + 4:
        return {"var_ratio": 1.0, "levene_p": 1.0}
    recent, earlier = resid[-sl:], resid[:-sl]

    def _s(x):  # robust: mix of sd and IQR/1.349 (insensitive to a single peak week)
        return max(1e-9, 0.5 * (np.std(x) + (np.subtract(*np.percentile(x, [75, 25])) / 1.349)))

    ratio = float(_s(recent) / _s(earlier))
    blocks = [
        resid[len(resid) - (i + 1) * sl : len(resid) - i * sl] for i in range(len(resid) // sl)
    ]
    lev = float(stats.levene(*blocks, center="median")[1]) if len(blocks) >= 2 else 1.0
    return {"var_ratio": ratio, "levene_p": lev}


def _hac_cov(X: np.ndarray, e: np.ndarray, lags: int) -> np.ndarray:
    n = len(e)
    xtxi = np.linalg.inv(X.T @ X)
    u = X * e[:, None]
    s = u.T @ u / n
    for lag in range(1, lags + 1):
        g = u[lag:].T @ u[:-lag] / n
        s += (1 - lag / (lags + 1)) * (g + g.T)
    return n * xtxi @ s @ xtxi


def level_shift_scan(d: np.ndarray, min_seg: int, step: int = 2, lags: int = 8) -> dict:
    """Scan for one level shift in ``d`` allowing a linear trend (so a smooth trend is not a
    break): OLS ``d ~ 1 + t + 1[t >= tau]`` for each candidate tau, HAC t-statistic of the step.
    Returns the tau with the largest |t| (a sup-t scan; the cutoff is a fixed conservative number,
    not an asymptotic critical value, because seasonal residuals are strongly autocorrelated)."""
    n = len(d)
    best = {"sup_t": 0.0, "tau": None, "shift": 0.0}
    if n < 2 * min_seg + 4:
        return best
    tt = np.arange(n) / n
    idx = np.arange(n)
    base = np.column_stack([np.ones(n), tt])
    for tau in range(min_seg, n - min_seg + 1, step):
        X = np.column_stack([base, (idx >= tau).astype(float)])
        beta, *_ = np.linalg.lstsq(X, d, rcond=None)
        e = d - X @ beta
        try:
            var = _hac_cov(X, e, lags)[2, 2]
        except np.linalg.LinAlgError:
            continue
        t = abs(beta[2]) / math.sqrt(max(var, 1e-18))
        if t > best["sup_t"]:
            best = {"sup_t": float(t), "tau": int(tau), "shift": float(beta[2])}
    return best


def hampel_outliers(z: np.ndarray, k: int = 3, z_thr: float = 6.0, min_dev: float = 0.5) -> dict:
    """Hampel identifier: deviation from the centred rolling median (window 2k+1) relative to the
    global robust scale of such deviations (floored at 0.1 model units because smooth epidemic
    curves make the MAD degenerate)."""
    s = pd.Series(z)
    med = s.rolling(2 * k + 1, center=True, min_periods=k + 1).median()
    dev = (s - med).to_numpy()
    scale = max(1.4826 * float(np.nanmedian(np.abs(dev))), 0.1)
    flag = (np.abs(dev) / scale > z_thr) & (np.abs(dev) > min_dev)
    flag = np.where(np.isnan(dev), False, flag)
    return {
        "flags": flag,
        "n": int(flag.sum()),
        "share": float(flag.mean()) if len(flag) else 0.0,
        "max_dev": float(np.nanmax(np.abs(dev))) if len(dev) else 0.0,
        "scale": scale,
    }


def tail_incomplete(
    z: np.ndarray,
    season_length: int,
    drop_z: float = 4.0,
    min_drop: float = 0.25,
    max_weeks: int = 3,
) -> dict:
    """Heuristic for an incompletely reported tail (no vintage data needed).

    ``r_t = z_t - z_{t-season}`` (year-over-year difference in model units) is smooth in a regular
    series. If the last k points (k <= max_weeks) all sit at least ``min_drop`` *below* the median
    of r just before the tail, and the average drop is ``drop_z`` robust SDs of historic
    k-step changes of r, the tail is flagged as probably provisional. Returns ``tail_weeks`` = 0
    when nothing is flagged. Reporting backfill is the commonest cause, but a real crash looks the
    same: hence a warning, never a refusal.
    """
    sl = season_length
    n = len(z)
    out = {"tail_weeks": 0, "drop": 0.0, "z": 0.0}
    if n < 2 * sl + 20:
        return out
    r = z[sl:] - z[:-sl]
    best = out
    for k in range(1, max_weeks + 1):
        ref = np.median(r[-(k + 8) : -k])
        resid = r[-k:] - ref
        hist = np.array(
            [
                np.median(r[i : i + k]) - np.median(r[max(0, i - 8) : i]) if i >= 8 else np.nan
                for i in range(0, len(r) - k)
            ]
        )
        hist = hist[~np.isnan(hist)]
        if len(hist) < 20:
            continue
        scale = max(1.4826 * float(np.median(np.abs(hist - np.median(hist)))), 0.05)
        drop = float(np.mean(resid))
        zz = drop / scale
        if np.all(resid < -min_drop) and zz < -drop_z:
            best = {"tail_weeks": k, "drop": drop, "z": float(zz)}
    return best


# ---------------------------------------------------------------------------------------------
# stage 1: data checks
# ---------------------------------------------------------------------------------------------


def _longest_run(mask: np.ndarray) -> int:
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def _trailing_run(mask: np.ndarray) -> int:
    k = 0
    for v in mask[::-1]:
        if not v:
            break
        k += 1
    return k


def assess_data(
    meta: SeriesMeta,
    grid: pd.DataFrame,
    g_model: pd.DataFrame | None = None,
    as_of: pd.Timestamp | None = None,
    cfg: Thresholds | None = None,
    sources: dict | None = None,
    masked: np.ndarray | None = None,
    check_stats: bool = True,
) -> GateDecision:
    """Stage-1 gate on the raw weekly grid (``ds``, ``y`` with NaN gaps) of one series.

    ``g_model`` is the fit-ready frame (short gaps interpolated, masked periods filled, ends
    trimmed); default: ``grid`` with ``interpolate(limit=max_gap)``. Heavy statistics (STL, ADF,
    KPSS, break scan) run only when no blocking data problem was found.
    """
    if cfg is None:
        cfg, sources = resolve_thresholds(
            meta.indicator_type, "W", meta.series_id, meta.gate or None
        )
    dec = GateDecision(
        thresholds=cfg,
        threshold_sources=sources or {},
        kind=meta.indicator_type,
        frequency=meta.frequency,
        as_of=None if as_of is None else as_of.date().isoformat(),
    )
    sl = meta.season_length
    y = grid["y"].to_numpy(dtype=float)
    n = len(y)
    m = dec.metrics
    m["n_periods"] = n
    m["season_length"] = sl
    m["seasons"] = n / sl if n else 0.0
    if meta.indicator_type == "event":
        dec.add(Reason("kind_event_not_forecast", "block"))
        return dec
    miss = np.isnan(y)
    n_missing = int(miss.sum())
    obs = y[~miss]
    m["n_missing"] = n_missing
    m["missing_frac"] = n_missing / n if n else 0.0
    m["data_from"] = None if not n else grid["ds"].iloc[0].date().isoformat()
    m["data_through"] = None if not n else grid["ds"].iloc[-1].date().isoformat()

    # --- history length ----------------------------------------------------------------------
    seasons = m["seasons"]
    if seasons < cfg.min_seasons_block:
        dec.add(
            Reason(
                "too_short",
                "block",
                {"n_periods": n, "seasons": seasons, "min_seasons": cfg.min_seasons_block},
                {"min_seasons_block": cfg.min_seasons_block},
            )
        )
    elif seasons < cfg.min_seasons_warn:
        dec.add(
            Reason(
                "short_history",
                "warn",
                {"seasons": seasons, "warn_seasons": cfg.min_seasons_warn},
                {"min_seasons_warn": cfg.min_seasons_warn},
            )
        )
    # --- gaps ----------------------------------------------------------------------------------
    if n:
        trailing = _trailing_run(miss)
        leading = 0
        for v in miss:
            if not v:
                break
            leading += 1
        interior = miss.copy()
        interior[n - trailing :] = False
        longest = _longest_run(interior)  # includes a leading run
        m.update(longest_gap=longest, trailing_gap=trailing, leading_gap=leading)
        gthr = {
            "max_gap": cfg.max_gap,
            "max_missing_frac_limit": cfg.max_missing_frac_limit,
            "max_missing_frac_block": cfg.max_missing_frac_block,
        }
        gmet = {
            "missing_pct": 100 * m["missing_frac"],
            "longest_gap": longest,
            "max_gap": cfg.max_gap,
            "thr_missing_pct": 100 * cfg.max_missing_frac_block,
        }
        if longest > cfg.max_gap or m["missing_frac"] > cfg.max_missing_frac_block:
            dec.add(Reason("too_many_gaps", "block", gmet, gthr))
        elif m["missing_frac"] > cfg.max_missing_frac_limit:
            dec.add(
                Reason(
                    "too_many_gaps",
                    "limit",
                    {**gmet, "thr_missing_pct": 100 * cfg.max_missing_frac_limit},
                    gthr,
                    {
                        "max_horizon_weeks": cfg.limited_max_horizon,
                        "interval_inflation": cfg.limited_interval_inflation,
                    },
                )
            )
        if trailing > cfg.max_trailing_gap:
            dec.add(
                Reason(
                    "long_gap_at_end",
                    "block",
                    {"trailing_gap": trailing},
                    {"max_trailing_gap": cfg.max_trailing_gap},
                )
            )
        elif trailing > 0:
            dec.add(
                Reason(
                    "long_gap_at_end",
                    "warn",
                    {"trailing_gap": trailing},
                    {"max_trailing_gap": cfg.max_trailing_gap},
                )
            )
    # --- timeliness --------------------------------------------------------------------------
    if as_of is not None and n:
        lag = int((as_of.normalize() - grid["ds"].iloc[-1]).days // 7)
        m["lag_periods"] = lag
        sv = {
            "lag_periods": lag,
            "as_of": dec.as_of,
            "warn_periods": cfg.stale_warn_periods,
            "block_periods": cfg.stale_block_periods,
        }
        st = {
            "stale_warn_periods": cfg.stale_warn_periods,
            "stale_block_periods": cfg.stale_block_periods,
        }
        if lag > cfg.stale_block_periods:
            dec.add(Reason("stale_data", "block", sv, st))
        elif lag > cfg.stale_warn_periods:
            dec.add(Reason("stale_data", "warn", sv, st))
    else:
        dec.skipped["stale_data"] = "no as_of date given (historical data are not judged stale)"
    # --- values --------------------------------------------------------------------------------
    if len(obs) == 0:
        dec.add(Reason("constant_series", "block", {"n_unique": 0, "rel_sd": 0.0}, {}))
        return dec
    if (obs < 0).any():
        dec.add(Reason("negative_values", "block", {"n_negative": int((obs < 0).sum())}))
    nz = float((obs > 0).mean())
    m["nonzero_share"] = nz
    nzv = obs[obs > 0]
    pos_idx = np.flatnonzero(np.nan_to_num(y) > 0)
    adi = float(n / len(pos_idx)) if len(pos_idx) else float("inf")
    cv2 = float((nzv.std() / nzv.mean()) ** 2) if len(nzv) > 1 and nzv.mean() > 0 else 0.0
    m.update(adi=adi if math.isfinite(adi) else None, cv2_nonzero=cv2)
    zmet = {
        "nonzero_pct": 100 * nz,
        "min_nonzero_pct": 100 * cfg.min_nonzero_block,
        "warn_nonzero_pct": 100 * cfg.min_nonzero_warn,
        "adi": adi if math.isfinite(adi) else 999.0,
        "cv2": cv2,
    }
    if nz < cfg.min_nonzero_block:
        dec.add(Reason("mostly_zeros", "block", zmet, {"min_nonzero_block": cfg.min_nonzero_block}))
    elif nz < cfg.min_nonzero_warn:
        dec.add(
            Reason("mostly_zeros_warn", "warn", zmet, {"min_nonzero_warn": cfg.min_nonzero_warn})
        )
    zrun = _longest_run(np.nan_to_num(y, nan=1.0) == 0)
    m["zero_run"] = zrun
    if zrun >= cfg.zero_run_warn and nz >= cfg.min_nonzero_block:
        dec.add(
            Reason("zero_runs", "warn", {"zero_run": zrun}, {"zero_run_warn": cfg.zero_run_warn})
        )
    mean = float(obs.mean())
    rel_sd = float(obs.std() / abs(mean)) if mean != 0 else 0.0
    n_unique = int(len(np.unique(np.round(obs, 12))))
    m.update(mean=mean, rel_sd=rel_sd, n_unique=n_unique)
    if n_unique <= 1 or (mean != 0 and rel_sd < cfg.constant_rel_sd):
        dec.add(
            Reason(
                "constant_series",
                "block",
                {"n_unique": n_unique, "rel_sd": rel_sd},
                {"constant_rel_sd": cfg.constant_rel_sd},
            )
        )
    if not check_stats or dec.refused:
        return dec

    # --- model-space statistics ---------------------------------------------------------------
    gm = g_model if g_model is not None else grid
    ym = gm["y"].to_numpy(dtype=float)
    if np.isnan(ym).any():  # only possible for non-default callers
        ym = pd.Series(ym).interpolate(limit_area="inside").bfill().ffill().to_numpy()
    tf: Transform = for_kind(meta.indicator_type, meta.value_scale)
    z = tf.forward(ym)
    m["model_periods"] = len(z)
    res = stl_decompose(z, sl)
    if res is None:
        dec.skipped["stl_checks"] = "series shorter than two seasons"
        return dec
    _decomposed_checks(dec, meta, z, res, cfg, gm)
    return dec


def _decomposed_checks(dec: GateDecision, meta: SeriesMeta, z, res, cfg: Thresholds, gm) -> None:
    m = dec.metrics
    sl = meta.season_length
    n = len(z)
    # seasonal strength -------------------------------------------------------------------------
    fs = seasonal_strength(res)
    m["seasonal_strength"] = fs
    expected = cfg.seasonal_expected == "true" or (cfg.seasonal_expected == "auto" and sl >= 4)
    if expected and fs < cfg.min_seasonal_strength:
        dec.add(
            Reason(
                "no_seasonal_signal_but_seasonal_expected",
                "warn",
                {
                    "season_length": sl,
                    "seasonal_strength": fs,
                    "min_strength": cfg.min_seasonal_strength,
                },
                {"min_seasonal_strength": cfg.min_seasonal_strength},
            )
        )
    d = z - res.seasonal  # deseasonalised level
    # stationarity (warning only) -----------------------------------------------------------------
    try:
        st = stationarity(d, cfg.adf_alpha, cfg.kpss_alpha, sl)
    except Exception as exc:  # pragma: no cover - statsmodels numerical failures
        st = None
        dec.skipped["stationarity"] = f"{type(exc).__name__}"
    if st:
        m["stationarity"] = st
        if (
            st["class"] in ("unit_root_like", "trend_or_break")
            and abs(st["drift"]) >= cfg.drift_warn
        ):
            dec.add(
                Reason(
                    "trend_nonstationary",
                    "warn",
                    {"adf_p": st["adf_p"], "kpss_p": st["kpss_p"], "drift": st["drift"]},
                    {
                        "adf_alpha": cfg.adf_alpha,
                        "kpss_alpha": cfg.kpss_alpha,
                        "drift_warn": cfg.drift_warn,
                    },
                )
            )
    # level shift ----------------------------------------------------------------------------------
    scan = level_shift_scan(d, max(8, sl // 2), step=max(1, sl // 26))
    m["level_shift"] = {k: scan[k] for k in ("sup_t", "shift")}
    if scan["tau"] is not None:
        bdate = gm["ds"].iloc[scan["tau"]].date().isoformat()
        m["level_shift"]["date"] = bdate
        if scan["sup_t"] >= cfg.break_min_sup_t and abs(scan["shift"]) >= cfg.break_min_shift:
            recent = (n - scan["tau"]) <= cfg.break_recent_seasons * sl
            dec.add(
                Reason(
                    "structural_break",
                    "limit" if recent else "warn",
                    {"break_date": bdate, "shift": scan["shift"], "sup_t": scan["sup_t"]},
                    {
                        "break_min_shift": cfg.break_min_shift,
                        "break_min_sup_t": cfg.break_min_sup_t,
                        "break_recent_seasons": cfg.break_recent_seasons,
                    },
                    {
                        "baseline_only": True,
                        "max_horizon_weeks": cfg.limited_max_horizon,
                        "interval_inflation": cfg.break_interval_inflation,
                    },
                    {
                        "where": "inside the most recent seasons of" if recent else "early in",
                        "where_zh": "的近期" if recent else "的较早阶段",
                        "where_fr": "dans les saisons récentes"
                        if recent
                        else "dans la partie ancienne",
                    },
                )
            )
    # variance -------------------------------------------------------------------------------------
    vs = variance_stability(res.resid, sl)
    m["variance"] = vs
    vr = vs["var_ratio"]
    if vr >= cfg.var_ratio_warn and vs["levene_p"] < 0.05:
        sev = "limit" if vr >= cfg.var_ratio_limit else "warn"
        dec.add(
            Reason(
                "unstable_variance",
                sev,
                {"var_ratio": vr, "levene_p": vs["levene_p"]},
                {"var_ratio_warn": cfg.var_ratio_warn, "var_ratio_limit": cfg.var_ratio_limit},
                {"interval_inflation": cfg.break_interval_inflation},
            )
        )
    # outliers ---------------------------------------------------------------------------------------
    ho = hampel_outliers(z, z_thr=cfg.outlier_z, min_dev=cfg.outlier_min_dev)
    m["outliers"] = {
        "n": ho["n"],
        "share": ho["share"],
        "max_dev": ho["max_dev"],
        "dates": [gm["ds"].iloc[i].date().isoformat() for i in np.flatnonzero(ho["flags"])[:10]],
    }
    if ho["share"] > cfg.outlier_share_warn:
        sev = "limit" if ho["share"] > cfg.outlier_share_limit else "warn"
        dec.add(
            Reason(
                "outlier_contamination",
                sev,
                {"n_outliers": ho["n"], "outlier_pct": 100 * ho["share"], "max_dev": ho["max_dev"]},
                {
                    "outlier_z": cfg.outlier_z,
                    "outlier_min_dev": cfg.outlier_min_dev,
                    "outlier_share_warn": cfg.outlier_share_warn,
                    "outlier_share_limit": cfg.outlier_share_limit,
                },
                {
                    "interval_inflation": cfg.limited_interval_inflation,
                    "max_horizon_weeks": cfg.limited_max_horizon,
                },
            )
        )
    # reporting-delay tail ---------------------------------------------------------------------------
    if cfg.reporting_lag_weeks > 0:
        k = cfg.reporting_lag_weeks
        m["tail"] = {"configured_lag_weeks": k}
        dec.add(
            Reason(
                "reporting_delay_tail_incomplete",
                "limit",
                {"tail_weeks": k, "tail_evidence": f"reporting_lag_weeks={k} configured"},
                {"reporting_lag_weeks": k},
                {"trim_tail_weeks": k},
                {
                    "tail_action": "excluded from model fitting",
                    "tail_action_zh": "排除在模型拟合之外",
                    "tail_action_fr": "exclues de l'ajustement",
                },
            )
        )
    else:
        tail = tail_incomplete(z, sl, cfg.tail_drop_z, cfg.tail_min_drop, cfg.tail_max_weeks)
        m["tail"] = tail
        if tail["tail_weeks"]:
            dec.add(
                Reason(
                    "reporting_delay_tail_incomplete",
                    "warn",
                    {
                        "tail_weeks": tail["tail_weeks"],
                        "tail_evidence": f"{tail['drop']:+.2f} model units vs the seasonal "
                        f"expectation, z={tail['z']:.1f}",
                    },
                    {
                        "tail_drop_z": cfg.tail_drop_z,
                        "tail_min_drop": cfg.tail_min_drop,
                        "tail_max_weeks": cfg.tail_max_weeks,
                    },
                    {},
                    {
                        "tail_action": "kept in the fit; set reporting_lag_weeks to exclude them",
                        "tail_action_zh": "仍用于拟合；可设置 reporting_lag_weeks 将其排除",
                        "tail_action_fr": "conservées dans l'ajustement ; définissez "
                        "reporting_lag_weeks pour les exclure",
                    },
                )
            )


# ---------------------------------------------------------------------------------------------
# stage 2: backtest checks
# ---------------------------------------------------------------------------------------------


def assess_backtest(
    dec: GateDecision,
    bt,
    chosen: str | None,
    h_ok: int,
    calibration: dict | None,
    baseline_only: bool = False,
) -> GateDecision:
    """Add the backtest-based reasons to a stage-1 decision (returns the same object)."""
    cfg = dec.thresholds
    dec.stage = "data+backtest"
    m = dec.metrics
    if bt is None:
        if not dec.refused:
            dec.add(
                Reason(
                    "insufficient_backtest",
                    "block",
                    {"n_windows": 0, "seasons_covered": 0.0, "min_windows": cfg.min_windows_block},
                    {"min_windows_block": cfg.min_windows_block},
                )
            )
        return dec
    m["backtest"] = {
        "n_windows": bt.n_windows,
        "seasons_covered": bt.seasons_covered,
        "chosen_model": chosen,
        "horizons_beating_baseline": h_ok,
    }
    bmet = {
        "n_windows": bt.n_windows,
        "seasons_covered": bt.seasons_covered,
        "min_windows": cfg.min_windows_limit,
    }
    lim = {
        "max_horizon_weeks": cfg.limited_max_horizon,
        "interval_inflation": cfg.limited_interval_inflation,
    }
    if bt.n_windows < cfg.min_windows_block:
        dec.add(
            Reason(
                "insufficient_backtest",
                "block",
                {**bmet, "min_windows": cfg.min_windows_block},
                {"min_windows_block": cfg.min_windows_block},
            )
        )
        return dec
    if bt.n_windows < cfg.min_windows_limit:
        dec.add(
            Reason(
                "insufficient_backtest",
                "limit",
                bmet,
                {"min_windows_limit": cfg.min_windows_limit},
                lim,
            )
        )
    if bt.seasons_covered < cfg.backtest_seasons_block:
        dec.add(
            Reason(
                "insufficient_backtest",
                "block",
                {**bmet, "min_windows": cfg.min_windows_limit},
                {"backtest_seasons_block": cfg.backtest_seasons_block},
            )
        )
    elif bt.seasons_covered < cfg.backtest_seasons_warn:
        dec.add(
            Reason(
                "short_backtest_span",
                "warn",
                {"seasons_covered": bt.seasons_covered, "min_seasons": cfg.backtest_seasons_warn},
                {"backtest_seasons_warn": cfg.backtest_seasons_warn},
            )
        )
    if baseline_only or chosen is None:
        m["backtest"]["skill_claimed"] = False
        return dec
    rel = bt.metrics[chosen]["rel_mae_vs_seasonal_naive"]
    m["backtest"]["rel_mae_vs_seasonal_naive"] = rel
    kmet = {
        "rel_mae": rel if rel is not None else float("nan"),
        "max_rel_mae": cfg.max_rel_mae,
        "marginal_rel_mae": cfg.marginal_rel_mae,
    }
    if h_ok == 0 or (rel is not None and rel >= cfg.max_rel_mae):
        dec.add(
            Reason("backtest_worse_than_baseline", "block", kmet, {"max_rel_mae": cfg.max_rel_mae})
        )
        return dec
    if h_ok < cfg.min_horizons_ok:
        dec.add(
            Reason(
                "skill_horizon_too_short",
                "block",
                {"h_ok": h_ok, "min_horizons": cfg.min_horizons_ok},
                {"min_horizons_ok": cfg.min_horizons_ok},
            )
        )
        return dec
    if rel is not None and rel >= cfg.marginal_rel_mae:
        dec.add(
            Reason("marginal_skill", "limit", kmet, {"marginal_rel_mae": cfg.marginal_rel_mae}, lim)
        )
    cov80 = (calibration or {}).get("80", {}).get("empirical_loo")
    m["backtest"]["coverage80_loo"] = cov80
    if cov80 is not None:
        cmet = {"cov80": cov80, "min_cov80": max(cfg.cov80_block, cfg.cov80_limit)}
        if cov80 < cfg.cov80_block:
            dec.add(
                Reason("interval_undercoverage", "block", cmet, {"cov80_block": cfg.cov80_block})
            )
        elif cov80 < cfg.cov80_limit:
            dec.add(
                Reason(
                    "interval_undercoverage",
                    "limit",
                    cmet,
                    {"cov80_limit": cfg.cov80_limit},
                    {"point_only": True},
                )
            )
    if chosen == "Naive":
        sev = "limit" if dec.has("trend_nonstationary") else "warn"
        dec.add(
            Reason(
                "no_skill_beyond_persistence",
                sev,
                {},
                {},
                {"max_horizon_weeks": cfg.limited_max_horizon},
            )
        )
    return dec


# ---------------------------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------------------------


@dataclass
class AlertGate:
    """Verdict for one alert rule on one series (latest period + whole history)."""

    status: str  # ok | degraded | refused
    confidence: str  # high | medium | low | none
    reasons: list[Reason]
    metrics: dict

    def to_dict(self) -> dict:
        rs = sorted(self.reasons, key=lambda r: SEVERITY_ORDER[r.severity])
        return {
            "status": self.status,
            "confidence": self.confidence,
            "reasons": [r.to_dict() for r in rs],
            "reason_codes": [r.code for r in rs],
            "metrics": _clean(self.metrics),
        }


def assess_alert(
    meta: SeriesMeta,
    y: np.ndarray,
    method: str,
    baseline: int,
    guard: int,
    cfg: Thresholds | None = None,
) -> AlertGate:
    """Minimum-data / baseline-quality gate for an alert rule (EARS, CUSUM, Farrington).

    The *latest* period is judged: a refusal means "no signal can be declared for this series
    now", never "no outbreak".
    """
    if cfg is None:
        cfg, _ = resolve_thresholds(meta.indicator_type, "W", meta.series_id, meta.gate or None)
    n = len(y)
    reasons: list[Reason] = []
    met: dict = {"n_periods": n, "baseline": baseline, "guard": guard}
    needed = baseline + guard + cfg.alert_min_eval_periods
    seasons = n / meta.season_length
    if method == "farrington" and meta.indicator_type == "proportion":
        reasons.append(
            Reason(
                "alert_method_not_applicable",
                "block",
                {"method": method, "kind": meta.indicator_type},
            )
        )
    elif method == "farrington":
        if seasons < cfg.farrington_min_seasons:
            reasons.append(
                Reason(
                    "alert_not_enough_seasons",
                    "block",
                    {
                        "method": method,
                        "min_seasons": cfg.farrington_min_seasons,
                        "seasons": seasons,
                    },
                    {"farrington_min_seasons": cfg.farrington_min_seasons},
                )
            )
    elif n < needed:
        reasons.append(
            Reason(
                "alert_baseline_too_short",
                "block",
                {
                    "n_periods": n,
                    "needed": needed,
                    "baseline": baseline,
                    "guard": guard,
                    "min_eval": cfg.alert_min_eval_periods,
                },
                {"alert_min_eval_periods": cfg.alert_min_eval_periods},
            )
        )
    if method != "farrington" and n > baseline + guard:
        w = y[n - guard - baseline : n - guard] if guard else y[n - baseline :]
        ww = w[~np.isnan(w)]
        need = max(3, int(cfg.alert_min_obs_frac * baseline))
        met["baseline_obs"] = int(len(ww))
        if len(ww) < need:
            reasons.append(
                Reason(
                    "alert_baseline_gappy",
                    "block",
                    {"obs": int(len(ww)), "baseline": baseline, "needed": need},
                    {"alert_min_obs_frac": cfg.alert_min_obs_frac},
                )
            )
        elif len(ww) >= 2:
            nzs = float((ww > 0).mean())
            mean = float(ww.mean())
            met.update(baseline_mean=mean, baseline_nonzero=nzs)
            if meta.indicator_type != "proportion" and (
                nzs < cfg.alert_min_baseline_nonzero or mean < cfg.alert_min_baseline_mean
            ):
                reasons.append(
                    Reason(
                        "alert_baseline_low_counts",
                        # sparse 'event' series are alerted on by design: warn, do not refuse
                        "warn" if meta.indicator_type == "event" else "block",
                        {"mean": mean, "nonzero_pct": 100 * nzs},
                        {
                            "alert_min_baseline_nonzero": cfg.alert_min_baseline_nonzero,
                            "alert_min_baseline_mean": cfg.alert_min_baseline_mean,
                        },
                    )
                )
            elif ww.std(ddof=1) == 0:
                reasons.append(Reason("alert_baseline_constant", "warn", {}, {}))
            # a previous outbreak inside the baseline: Hampel on the baseline window itself
            tf = for_kind(meta.indicator_type, meta.value_scale)
            zz = tf.forward(ww)
            if len(zz) >= 5:
                ho = hampel_outliers(zz, k=2, z_thr=cfg.outlier_z, min_dev=cfg.outlier_min_dev)
                if ho["n"] > 0:
                    reasons.append(
                        Reason(
                            "alert_baseline_contaminated",
                            "warn",
                            {"n_outliers": ho["n"]},
                            {"outlier_z": cfg.outlier_z},
                        )
                    )
    if any(r.severity == "block" for r in reasons):
        return AlertGate("refused", "none", reasons, met)
    if reasons:
        return AlertGate("degraded", "low", reasons, met)
    conf = "high" if (n >= needed + 2 * baseline and seasons >= 2) else "medium"
    return AlertGate("ok", conf, reasons, met)
