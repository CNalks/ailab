"""Aberration-detection signals: EARS C1/C2/C3 (CDC) and a standardised CUSUM.

Weekly adaptation: baseline = the ``baseline`` weeks before the current week, with a guard band of
0 (C1) or 2 (C2/C3) weeks. A signal is a *statistical* signal, not a declaration of an outbreak,
and carries no colour/risk level. Seasonality is not modelled here: during a normal seasonal rise
these methods will signal, by design.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import AlertRule

DEFAULT_THRESHOLD = {"ears_c1": 3.0, "ears_c2": 3.0, "ears_c3": 2.0, "cusum": 4.0}
GUARD = {"ears_c1": 0, "ears_c2": 2, "ears_c3": 2, "cusum": 2}
CUSUM_K = 0.5
MIN_BASELINE_OBS_FRAC = 0.7


def _z_scores(y: np.ndarray, baseline: int, guard: int) -> tuple[np.ndarray, np.ndarray]:
    n = len(y)
    z = np.full(n, np.nan)
    mu = np.full(n, np.nan)
    for t in range(baseline + guard, n):
        w = y[t - guard - baseline : t - guard]
        w = w[~np.isnan(w)]
        if len(w) < max(3, int(MIN_BASELINE_OBS_FRAC * baseline)) or np.isnan(y[t]):
            continue
        m, sd = float(w.mean()), float(w.std(ddof=1))
        sd = max(sd, 1e-6, 0.05 * abs(m))  # floor: avoids infinite scores on flat baselines
        mu[t] = m
        z[t] = (y[t] - m) / sd
    return z, mu


def detect(
    df: pd.DataFrame, method: str = "ears_c2", baseline: int = 7, threshold: float | None = None
) -> pd.DataFrame:
    """Return per-week frame: ds, observed, expected, score, threshold, alarm."""
    if method not in DEFAULT_THRESHOLD:
        raise ValueError(f"unknown method {method!r}")
    thr = DEFAULT_THRESHOLD[method] if threshold is None else float(threshold)
    g = df.sort_values("ds").reset_index(drop=True)
    y = g["y"].to_numpy(dtype=float)
    z, mu = _z_scores(y, baseline, GUARD[method])
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


def evaluate_rule(rule: AlertRule, df: pd.DataFrame) -> pd.DataFrame:
    return detect(df, rule.method, rule.baseline, rule.threshold)


def summarise(res: pd.DataFrame, recent: int = 12) -> dict:
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
