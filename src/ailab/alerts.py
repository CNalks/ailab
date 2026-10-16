"""Aberration-detection signals: EARS C1/C2/C3 (CDC), a standardised CUSUM and a
Farrington-flexible-style seasonal GLM threshold.

Weekly adaptation: baseline = the ``baseline`` weeks before the current week, with a guard band of
0 (C1) or 2 (C2/C3) weeks. A signal is a *statistical* signal, not a declaration of an outbreak,
and carries no colour/risk level. Seasonality is not modelled here: during a normal seasonal rise
these methods will signal, by design.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .schema import AlertRule

DEFAULT_THRESHOLD = {
    "ears_c1": 3.0,
    "ears_c2": 3.0,
    "ears_c3": 2.0,
    "cusum": 4.0,
    "farrington": 2.0,  # benchmarks/alert_bench.py: ~1 false alarm / 100 clean weeks
}
GUARD = {"ears_c1": 0, "ears_c2": 2, "ears_c3": 2, "cusum": 2, "farrington": 0}
FARRINGTON_B = 5  # reference years (at most)
FARRINGTON_W = 3  # half-width (weeks) of the reference window around the same week of each year
CUSUM_K = 0.5
MIN_BASELINE_OBS_FRAC = 0.7


def _z_scores(y: np.ndarray, baseline: int, guard: int, cfg=None) -> tuple[np.ndarray, np.ndarray]:
    n = len(y)
    z = np.full(n, np.nan)
    mu = np.full(n, np.nan)
    for t in range(baseline + guard, n):
        w = y[t - guard - baseline : t - guard]
        w = w[~np.isnan(w)]
        if len(w) < max(3, int(MIN_BASELINE_OBS_FRAC * baseline)) or np.isnan(y[t]):
            continue
        m, sd = float(w.mean()), float(w.std(ddof=1))
        if cfg is not None and (
            m < cfg.alert_min_baseline_mean
            or float((w > 0).mean()) < cfg.alert_min_baseline_nonzero
        ):
            continue  # baseline of (almost) zeros: a z-score is meaningless
        sd = max(sd, 1e-6, 0.05 * abs(m))  # floor: avoids infinite scores on flat baselines
        mu[t] = m
        z[t] = (y[t] - m) / sd
    return z, mu


def farrington_scores(
    y: np.ndarray,
    season_length: int = 52,
    b: int = FARRINGTON_B,
    w: int = FARRINGTON_W,
    min_years: int = 3,
    trend: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Farrington-flexible-style score per week (simplified; see docs/BENCHMARK.md).

    For week t the reference values are the weeks ``t - k*season +- w`` of the last ``b`` seasons
    (k = 1..b). A quasi-Poisson GLM (log link, optional linear trend) is fitted to them with one
    re-weighting pass that down-weights past outbreaks (|Pearson residual| > 2.58). The score is the
    standardised excess on the 2/3-power scale (Farrington et al. 1996 prediction variance
    ``4/9 (phi mu^{1/3} + mu^{4/3} var(eta))``); a score above ``z`` means the observation exceeds
    the ``1 - Phi(z)`` upper prediction limit. Weeks with fewer than ``min_years`` reference seasons
    are NaN. Returns (score, expected).
    """
    import warnings

    import statsmodels.api as sm

    n = len(y)
    score = np.full(n, np.nan)
    mu_out = np.full(n, np.nan)
    for t in range(min_years * season_length - w, n):
        if np.isnan(y[t]):
            continue
        idx, tt = [], []
        for k in range(1, b + 1):
            c = t - k * season_length
            for j in range(-w, w + 1):
                i = c + j
                if 0 <= i < t - 1 and not np.isnan(y[i]):  # strictly past, not the previous week
                    idx.append(i)
                    tt.append(-k)
        if len({(-v) for v in tt}) < min_years or len(idx) < 8:
            continue
        yy = y[idx]
        if yy.sum() <= 0:
            continue
        X = np.column_stack([np.ones(len(idx)), tt]) if trend else np.ones((len(idx), 1))
        wts = np.ones(len(idx))
        try:
            with warnings.catch_warnings(), np.errstate(all="ignore"):
                warnings.simplefilter("ignore")
                for _ in range(2):
                    fit = sm.GLM(yy, X, family=sm.families.Poisson(), var_weights=wts).fit(
                        scale="x2"
                    )
                    r0 = (yy - fit.mu) / np.sqrt(np.maximum(fit.mu, 1e-9))
                    # robust dispersion for the weights: a past outbreak must not inflate the
                    # scale that is used to detect it
                    phi_r = max(1.0, (1.4826 * np.median(np.abs(r0 - np.median(r0)))) ** 2)
                    r = r0 / math.sqrt(phi_r)
                    wts = np.where(np.abs(r) > 2.58, (2.58 / np.maximum(np.abs(r), 1e-9)) ** 2, 1.0)
            x0 = np.array([1.0, 0.0]) if trend else np.array([1.0])
            eta = float(x0 @ fit.params)
            with np.errstate(all="ignore"):
                var_eta = float(x0 @ fit.cov_params() @ x0)
        except Exception:  # singular design / non-convergence: no score for this week
            continue
        mu = math.exp(eta)
        phi = max(fit.scale, 1.0)
        var23 = (4.0 / 9.0) * (phi * mu ** (1 / 3) + mu ** (4 / 3) * var_eta)
        score[t] = (max(y[t], 0.0) ** (2 / 3) - mu ** (2 / 3)) / math.sqrt(max(var23, 1e-12))
        mu_out[t] = mu
    return score, mu_out


def detect(
    df: pd.DataFrame,
    method: str = "ears_c2",
    baseline: int = 7,
    threshold: float | None = None,
    season_length: int = 52,
    cfg=None,
) -> pd.DataFrame:
    """Return per-week frame: ds, observed, expected, score, threshold, alarm.
    ``cfg`` (``gate.Thresholds``) switches on per-week suppression of scores whose baseline is
    (almost) all zeros / very low counts.

    ``farrington`` ignores ``baseline`` (its reference weeks come from previous seasons)."""
    if method not in DEFAULT_THRESHOLD:
        raise ValueError(f"unknown method {method!r}")
    thr = DEFAULT_THRESHOLD[method] if threshold is None else float(threshold)
    g = df.sort_values("ds").reset_index(drop=True)
    y = g["y"].to_numpy(dtype=float)
    if method == "farrington":
        score, mu = farrington_scores(y, season_length)
        return pd.DataFrame(
            {
                "ds": g["ds"],
                "observed": y,
                "expected": mu,
                "score": score,
                "threshold": thr,
                "alarm": np.where(np.isnan(score), False, score > thr),
            }
        )
    z, mu = _z_scores(y, baseline, GUARD[method], cfg)
    if method in ("ears_c1", "ears_c2"):
        score = z
    elif method == "ears_c3":
        c = np.where(np.isnan(z), np.nan, np.maximum(0.0, z - 1.0))
        score = np.full(len(y), np.nan)
        for t in range(2, len(y)):
            win = c[t - 2 : t + 1]
            if not np.isnan(win).any():
                score[t] = win.sum()
    else:  # cusum
        score = np.full(len(y), np.nan)
        s = 0.0
        for t in range(len(y)):
            if np.isnan(z[t]):
                continue
            s = max(0.0, s + z[t] - CUSUM_K)
            score[t] = s
            if s > thr:
                s = 0.0  # restart after a signal
    out = pd.DataFrame(
        {
            "ds": g["ds"],
            "observed": y,
            "expected": mu,
            "score": score,
            "threshold": thr,
            "alarm": np.where(np.isnan(score), False, score > thr),
        }
    )
    return out


def evaluate_rule(rule: AlertRule, df: pd.DataFrame, season_length: int = 52) -> pd.DataFrame:
    return detect(df, rule.method, rule.baseline, rule.threshold, season_length)


def summarise(res: pd.DataFrame, recent: int = 12, gate=None) -> dict:
    """Summary of one scan. ``gate`` (a ``gate.AlertGate``): when it *refuses*, the history is
    still listed but no signal is declared (``latest_is_signal`` False, ``signal_periods`` empty,
    ``n_signals_suppressed`` says how many raw signals were withheld) and ``status`` carries the
    reason codes."""
    out = _summarise(res, recent)
    if gate is not None:
        out["status"] = gate.status
        out["confidence"] = gate.confidence
        out["gate"] = gate.to_dict()
        if gate.status == "refused":
            out["n_signals_suppressed"] = out["n_signals"]
            out.update(n_signals=0, latest_is_signal=False, signal_periods=[])
            for r in out["recent"]:
                r["alarm"] = False
            if out["latest"]:
                out["latest"]["alarm"] = False
    return out


def scan(
    meta,
    df: pd.DataFrame,
    method: str,
    baseline: int = 7,
    threshold: float | None = None,
    recent: int = 12,
) -> dict:
    """Gated scan of one series (the entry point used by the API, CLI and pipeline)."""
    from . import gate as gate_mod
    from . import quality

    cfg, _ = gate_mod.resolve_thresholds(meta.indicator_type, "W", meta.series_id, meta.gate)
    g, _n = quality.model_input(df)
    if meta.mask_periods:
        ds = g["ds"]
        mk = pd.Series(False, index=g.index)
        for a, b in meta.mask_periods:
            mk |= (ds >= pd.Timestamp(a)) & (ds <= pd.Timestamp(b))
        g.loc[mk, "y"] = np.nan
    ag = gate_mod.assess_alert(
        meta, g["y"].to_numpy(dtype=float), method, baseline, GUARD[method], cfg
    )
    res = detect(g, method, baseline, threshold, meta.season_length, cfg)
    return summarise(res, recent, ag)


def _summarise(res: pd.DataFrame, recent: int = 12) -> dict:
    ok = res.dropna(subset=["score"])
    sig = ok[ok["alarm"]]
    last = ok.iloc[-1] if len(ok) else None
    return {
        "n_evaluated": int(len(ok)),
        "n_signals": int(len(sig)),
        "latest": None if last is None else _row(last),
        "latest_is_signal": bool(last["alarm"]) if last is not None else False,
        "recent": [_row(r) for _, r in ok.tail(recent).iterrows()],
        "signal_periods": [r.ds.date().isoformat() for _, r in sig.iterrows()],
        "wording": "statistical signal vs recent baseline; not an outbreak declaration",
    }


def _row(r: pd.Series) -> dict:
    return {
        "ds": r["ds"].date().isoformat(),
        "observed": _f(r["observed"]),
        "expected": _f(r["expected"]),
        "score": _f(r["score"]),
        "threshold": _f(r["threshold"]),
        "alarm": bool(r["alarm"]),
    }


def _f(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), 5)
