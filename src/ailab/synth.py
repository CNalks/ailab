"""Deterministic synthetic weekly series for development and tests (no network, seeded).

Covers seasonality, trend, missing weeks, outbreak spikes, overdispersed counts and proportions
(binomial noise with a varying denominator). Same arguments -> identical output.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

START = "2012-01-01"  # a Sunday


def _seasonal(n: int, season_length: int, sharpness: float) -> np.ndarray:
    """0..1 seasonal profile peaking once per season (flu-like: sharp winter peak)."""
    t = np.arange(n)
    return np.maximum(0.0, np.sin(2 * np.pi * t / season_length - 0.5)) ** sharpness


def _bumps(n: int, outbreaks: Sequence[tuple[int, float, float]]) -> np.ndarray:
    """Multiplicative outbreak bumps: (centre_week, extra_fraction, width_weeks)."""
    t = np.arange(n)
    m = np.zeros(n)
    for centre, height, width in outbreaks:
        m += height * np.exp(-0.5 * ((t - centre) / max(width, 0.5)) ** 2)
    return m


def _apply_missing(y: np.ndarray, missing: Sequence[int], missing_frac: float, seed: int):
    y = y.astype(float).copy()
    for i in missing:
        y[i] = np.nan
    if missing_frac > 0:
        rng = np.random.default_rng(seed + 10_007)
        y[rng.random(len(y)) < missing_frac] = np.nan
    return y


def synth_series(
    n_weeks: int = 52 * 6,
    kind: str = "count",
    seed: int = 0,
    season_length: int = 52,
    level: float | None = None,
    amplitude: float = 3.0,
    sharpness: float = 2.0,
    trend: float = 0.0,
    dispersion: float = 0.05,
    outbreaks: Sequence[tuple[int, float, float]] = (),
    missing: Sequence[int] = (),
    missing_frac: float = 0.0,
    denominator: int = 2000,
    value_scale: int = 1,
    start: str = START,
) -> pd.DataFrame:
    """Return a frame ``ds, y``.

    * ``kind="count"``: negative-binomial counts around ``level * (1 + amplitude * season)``;
      ``dispersion`` is the squared coefficient of variation of the gamma mixing
      (``level`` defaults to 100).
    * ``kind="proportion"``: binomial(denominator_t, p_t) / denominator_t with baseline
      probability ``level`` (a fraction, default 0.03); values are multiplied by ``value_scale``
      (1 = fraction, 100 = percent).
    * ``outbreaks``: ``(centre_week, extra_fraction, width_weeks)`` multiplicative spikes.
    * ``missing`` indices / ``missing_frac`` become NaN (never 0).
    """
    if kind not in ("count", "proportion"):
        raise ValueError("kind must be 'count' or 'proportion'")
    if level is None:
        level = 100.0 if kind == "count" else 0.03
    rng = np.random.default_rng(seed)
    season = _seasonal(n_weeks, season_length, sharpness)
    mult = (1.0 + amplitude * season) * (1.0 + trend * np.arange(n_weeks) / n_weeks)
    mult = mult * (1.0 + _bumps(n_weeks, outbreaks))
    if kind == "count":
        mu = level * mult
        if dispersion > 0:
            mu = mu * rng.gamma(1 / dispersion, dispersion, n_weeks)
        y = rng.poisson(mu).astype(float)
    else:
        p = np.clip(level * mult, 1e-6, 0.95)
        n_t = rng.poisson(denominator * (1.0 + 0.2 * np.sin(np.arange(n_weeks) / 5.0))) + 1
        y = rng.binomial(n_t, p) / n_t * value_scale
    ds = pd.date_range(start, periods=n_weeks, freq="7D")
    return pd.DataFrame({"ds": ds, "y": _apply_missing(y, missing, missing_frac, seed)})


def synth_pair(
    n_weeks: int = 52 * 6,
    seed: int = 0,
    season_length: int = 52,
    tests: float = 1000.0,
    positivity: float = 0.05,
    amplitude: float = 3.0,
    outbreaks: Sequence[tuple[int, float, float]] = (),
    missing_num: Sequence[int] = (),
    missing_den: Sequence[int] = (),
    start: str = START,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(positives, tests) count series with ``positives <= tests`` (so positives / tests is a
    valid proportion), seasonal positivity, optional outbreak spikes and gaps."""
    rng = np.random.default_rng(seed)
    season = _seasonal(n_weeks, season_length, 2.0)
    n_t = rng.poisson(tests * (1.0 + 0.3 * season)) + 1
    p = np.clip(
        positivity * (1.0 + amplitude * season) * (1.0 + _bumps(n_weeks, outbreaks)), 0, 0.95
    )
    pos = rng.binomial(n_t, p).astype(float)
    ds = pd.date_range(start, periods=n_weeks, freq="7D")
    num = pd.DataFrame({"ds": ds, "y": _apply_missing(pos, missing_num, 0, seed)})
    den = pd.DataFrame({"ds": ds, "y": _apply_missing(n_t.astype(float), missing_den, 0, seed)})
    return num, den
