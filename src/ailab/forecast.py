"""Baseline forecasting with honest evaluation.

* Models (statsforecast, CPU only): SeasonalNaive (the baseline to beat), Naive, MSTL (seasonal
  decomposition + ETS trend), AutoTheta. (Plain AutoETS is not used: statsforecast does not fit a
  seasonal ETS at weekly period 52, so it collapses to Naive.)
* Rolling-origin backtest: many forecast origins x horizons; no data after an origin is ever used.
* Intervals: empirical absolute-error quantiles per horizon from the backtest (split-conformal),
  with a leave-one-window-out coverage check reported next to the nominal level.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsforecast import StatsForecast
from statsforecast.models import MSTL, AutoETS, AutoTheta, Naive, SeasonalNaive

from .transforms import Transform, for_kind

BASELINE = "SeasonalNaive"
CANDIDATES = ("MSTL", "AutoTheta", "Naive")
FAMILY = {
    "MSTL": "mstl_ets",
    "AutoTheta": "theta",
    "Naive": "naive",
    "SeasonalNaive": "seasonal_naive",
}
LEVELS = (0.80, 0.95)


def _sf(season_length: int) -> StatsForecast:
    models = [
        SeasonalNaive(season_length=season_length),
        Naive(),
        MSTL(season_length=season_length, trend_forecaster=AutoETS(model="ZZN")),
        AutoTheta(season_length=season_length),
    ]
    return StatsForecast(models=models, freq="7D", n_jobs=1)


def _frame(g: pd.DataFrame, tf: Transform | None = None) -> pd.DataFrame:
    y = g["y"].to_numpy(dtype=float)
    if tf is not None:
        y = np.where(np.isnan(y), np.nan, tf.forward(y))
    return pd.DataFrame({"unique_id": "s", "ds": g["ds"], "y": y})


@dataclass
class Backtest:
    cv: pd.DataFrame  # unique_id, ds, cutoff, y, <models>, h   (original scale)
    cv_z: pd.DataFrame  # same, in the transformed (model) space: defines the intervals
    n_windows: int
    horizon: int
    step: int
    scale: float
    metrics: dict
    transform: Transform
    seasons_covered: float  # span from first origin to last target, in seasons

    def abs_errors(self, model: str) -> dict[int, np.ndarray]:
        """Absolute backtest errors per horizon in the transformed space."""
        out = {}
        for h, grp in self.cv_z.groupby("h"):
            out[int(h)] = (grp["y"] - grp[model]).abs().to_numpy()
        return out


def windows_for_seasons(seasons: float, season_length: int, step: int) -> int:
    """Number of rolling origins needed so that the backtest spans ``seasons`` seasons."""
    return max(5, math.ceil(seasons * season_length / step))


def rolling_origin_backtest(
    g: pd.DataFrame,
    season_length: int = 52,
    horizon: int = 8,
    n_windows: int = 26,
    step: int = 2,
    transform: Transform | None = None,
    score_mask: np.ndarray | None = None,
) -> Backtest | None:
    """``g`` must be a regular weekly frame without NaN. Returns None if too short.

    Models are fitted on ``transform.forward(y)``; the returned ``cv`` is back-transformed so
    metrics are in original units, ``cv_z`` keeps the model space for the interval calibration.
    """
    tf = transform or Transform("identity")
    n = len(g)
    max_windows = (n - 2 * season_length - horizon) // step + 1
    n_windows = min(n_windows, max_windows)
    if n_windows < 5 or g["y"].isna().any():
        return None
    cv_z = _sf(season_length).cross_validation(
        df=_frame(g, tf), h=horizon, step_size=step, n_windows=n_windows
    )
    cv_z = cv_z.reset_index() if "unique_id" not in cv_z.columns else cv_z
    cv_z["h"] = ((cv_z["ds"] - cv_z["cutoff"]).dt.days // 7).astype(int)
    if score_mask is not None and score_mask.any():
        masked_ds = set(g["ds"][np.asarray(score_mask, dtype=bool)])
        cv_z = cv_z[~cv_z["ds"].isin(masked_ds)].reset_index(drop=True)
        if cv_z["cutoff"].nunique() < 5:
            return None
    cv = cv_z.copy()
    for col in ("y", BASELINE, *CANDIDATES):
        cv[col] = tf.inverse(cv_z[col].to_numpy())
    # observed truth exactly as stored (avoid round-trip noise through the transform)
    cv["y"] = g.set_index("ds")["y"].reindex(cv["ds"]).to_numpy()
    first_cutoff = cv["cutoff"].min()
    hist = g.loc[g["ds"] <= first_cutoff, "y"].to_numpy()
    scale = float(np.mean(np.abs(hist[season_length:] - hist[:-season_length])))
    scale = scale if scale > 0 else float("nan")
    metrics: dict[str, dict] = {}
    base_err = (cv["y"] - cv[BASELINE]).abs()
    base_mae = float(base_err.mean())
    base_by_h = base_err.groupby(cv["h"]).mean()
    for m in (BASELINE, *CANDIDATES):
        err = (cv["y"] - cv[m]).abs()
        mae = float(err.mean())
        by_h = err.groupby(cv["h"]).mean()
        metrics[m] = {
            "mae": mae,
            "mase": mae / scale if scale == scale else None,
            "rel_mae_vs_seasonal_naive": mae / base_mae if base_mae > 0 else None,
            "mae_by_horizon": {int(h): float(v) for h, v in by_h.items()},
            "beats_baseline_by_horizon": {int(h): bool(by_h[h] < base_by_h[h]) for h in by_h.index},
        }
    span_weeks = int((cv["ds"].max() - first_cutoff).days // 7)
    return Backtest(
        cv,
        cv_z,
        int(cv["cutoff"].nunique()),
        horizon,
        step,
        scale,
        metrics,
        tf,
        round(span_weeks / season_length, 2),
    )


def choose_model(bt: Backtest, candidates: tuple[str, ...] = CANDIDATES) -> tuple[str, int]:
    """Best candidate by overall MAE and the number of leading horizons where it beats baseline."""
    best = min(candidates, key=lambda m: bt.metrics[m]["mae"])
    flags = bt.metrics[best]["beats_baseline_by_horizon"]
    h_ok = 0
    for h in range(1, bt.horizon + 1):
        if flags.get(h):
            h_ok = h
        else:
            break
    return best, h_ok


def _quantile(errors: np.ndarray, level: float) -> tuple[float, bool]:
    n = len(errors)
    k = math.ceil((n + 1) * level)
    s = np.sort(errors)
    if k > n:
        return float(s[-1]), False  # not enough windows for a valid finite-sample quantile
    return float(s[k - 1]), True


def interval_widths(bt: Backtest, model: str) -> dict:
    """Per-horizon half-widths and leave-one-window-out empirical coverage."""
    errs = bt.abs_errors(model)
    widths: dict[int, dict[float, float]] = {}
    exact = True
    cover = {lv: [] for lv in LEVELS}
    for h, a in errs.items():
        widths[h] = {}
        for lv in LEVELS:
            q, ok = _quantile(a, lv)
            widths[h][lv] = q
            exact &= ok
            for i in range(len(a)):
                rest = np.delete(a, i)
                qi, _ = _quantile(rest, lv)
                cover[lv].append(bool(a[i] <= qi))
    calib = {
        f"{int(lv * 100)}": {
            "nominal": lv,
            "empirical_loo": round(float(np.mean(cover[lv])), 4) if cover[lv] else None,
        }
        for lv in LEVELS
    }
    return {
        "half_widths": widths,
        "calibration": calib,
        "n_windows": bt.n_windows,
        "finite_sample_valid": exact,
    }


def inflate(widths: dict, factor: float) -> dict:
    """Scale all half-widths (transformed space) by ``factor`` (used by limited mode)."""
    if factor == 1.0:
        return widths
    hw = {h: {lv: w * factor for lv, w in d.items()} for h, d in widths["half_widths"].items()}
    return {**widths, "half_widths": hw, "inflation": factor}


def forecast_point(
    g: pd.DataFrame, model: str, horizon: int, season_length: int, tf: Transform | None = None
) -> pd.DataFrame:
    """Point forecast in the *transformed* space (column ``z``)."""
    fc = _sf(season_length).forecast(df=_frame(g, tf), h=horizon)
    fc = fc.reset_index() if "unique_id" not in fc.columns else fc
    return fc[["ds", model]].rename(columns={model: "z"})


def build_forecast(
    g: pd.DataFrame,
    model: str,
    widths: dict,
    horizon: int,
    season_length: int,
    transform: Transform,
    band: bool,
) -> dict:
    """Forecast with interval ends computed in the transformed space, then back-transformed.

    The result is therefore always inside the kind's valid range (>= 0; <= scale for proportions)
    and intervals are asymmetric on the original scale (wider upward for counts).
    """
    pts = forecast_point(g, model, horizon, season_length, transform)
    rows = []
    for i, r in enumerate(pts.itertuples(index=False), start=1):
        row = {"h": i, "ds": r.ds.date().isoformat(), "point": _r(transform.inverse(r.z))}
        if band:
            for lv in LEVELS:
                w = widths["half_widths"][i][lv]
                tag = int(lv * 100)
                row[f"lo{tag}"] = _r(transform.inverse(r.z - w))
                row[f"hi{tag}"] = _r(transform.inverse(r.z + w))
        rows.append(row)
    return {
        "origin": g["ds"].iloc[-1].date().isoformat(),
        "model": model,
        "transform": transform.name,
        "interval_method": "empirical_abs_error_quantile_per_horizon (split-conformal, "
        "computed in transformed space)",
        "points": rows,
    }


def _r(x) -> float:
    return round(float(x), 5)


__all__ = [
    "BASELINE",
    "CANDIDATES",
    "FAMILY",
    "LEVELS",
    "Backtest",
    "build_forecast",
    "choose_model",
    "for_kind",
    "interval_widths",
    "rolling_origin_backtest",
    "windows_for_seasons",
]
