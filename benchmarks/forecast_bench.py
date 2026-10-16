"""Forecast benchmark: model choice, interval calibration, COVID-like masking.

Offline and deterministic (seeded synthetic scenarios + the bundled real FluView fixture).
Run:  uv run python benchmarks/forecast_bench.py [--quick] [--out benchmarks/results/forecast.json]

For every series a rolling-origin cross-validation (h=8, step=3) is run with each candidate;
errors are in original units. Intervals are *out-of-sample*: the calibration errors come from the
first 60 % of the origins, coverage/WIS are scored on the last 40 % (origins closer than h to
the calibration period are purged), so reported coverage is not the in-sample leave-one-out figure.
"""

from __future__ import annotations

import argparse
import json
import math
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

H, STEP = 8, 3
SL = 52
LEVELS = (0.80, 0.95)
MODELS = ["SeasonalNaive", "Naive", "SWA3", "MSTL", "MSTL_ARIMA", "AutoTheta"]
ENSEMBLES = {
    "ENS_mstl_theta": ["MSTL", "AutoTheta"],
    "ENS_mstl_theta_swa": ["MSTL", "AutoTheta", "SWA3"],
    "ENS_mstl_theta_naive": ["MSTL", "AutoTheta", "Naive"],
}
ALL = MODELS + list(ENSEMBLES)


def _sf(season_length: int):
    from statsforecast import StatsForecast
    from statsforecast.models import (
        MSTL,
        AutoARIMA,
        AutoETS,
        AutoTheta,
        Naive,
        SeasonalNaive,
        SeasonalWindowAverage,
    )

    return StatsForecast(
        models=[
            SeasonalNaive(season_length=season_length),
            Naive(),
            SeasonalWindowAverage(season_length=season_length, window_size=3, alias="SWA3"),
            MSTL(season_length=season_length, trend_forecaster=AutoETS(model="ZZN")),
            MSTL(
                season_length=season_length,
                trend_forecaster=AutoARIMA(max_p=2, max_q=2, max_d=1),
                alias="MSTL_ARIMA",
            ),
            AutoTheta(season_length=season_length),
        ],
        freq="7D",
        n_jobs=1,
    )


def scenarios(quick: bool) -> list[dict]:
    from ailab.synth import synth_series

    seeds = range(2 if quick else 6)
    out = []
    n = 52 * 8
    for sd in seeds:
        out.append(dict(name="seasonal", seed=sd, kind="count", df=synth_series(n, "count", sd)))
        out.append(
            dict(name="seasonal_trend", seed=sd, kind="count", df=synth_series(n, "count", sd, trend=2.0))
        )
        out.append(
            dict(
                name="seasonal_lowcount",
                seed=sd,
                kind="count",
                df=synth_series(n, "count", sd, level=4.0, amplitude=4.0),
            )
        )
        out.append(
            dict(name="seasonal_prop", seed=sd, kind="proportion", df=synth_series(n, "proportion", sd))
        )
        out.append(
            dict(
                name="flat_nonseasonal",
                seed=sd,
                kind="count",
                df=synth_series(n, "count", sd, amplitude=0.0, dispersion=0.02),
            )
        )
        df = synth_series(n, "count", sd, trend=0.5)
        rng = np.random.default_rng(sd)
        idx = rng.choice(np.arange(60, n - 10), 16, replace=False)
        y = df["y"].to_numpy().copy()
        y[idx] *= rng.choice([5.0, 0.05], 16)
        out.append(dict(name="seasonal_outliers", seed=sd, kind="count", df=df.assign(y=np.round(y))))
        df = synth_series(n, "count", sd)
        y = df["y"].to_numpy().copy()
        y[200:262] = np.round(y[200:262] * 0.05)  # COVID-like suppression (~1 year)
        out.append(
            dict(
                name="covid_like",
                seed=sd,
                kind="count",
                df=df.assign(y=y),
                mask=[[str(df["ds"][200].date()), str(df["ds"][261].date())]],
                origin_min=270,
            )
        )
    # real FluView fixture (final data)
    from ailab.sources import fluview

    raw = fluview.fixture_dataframe()
    w = fluview.to_series(raw, "wili")
    c = fluview.to_series(raw, "num_ili")
    out.append(dict(name="real_wili", seed=0, kind="proportion", scale=100, df=w))
    out.append(dict(name="real_num_ili", seed=0, kind="count", df=c))
    cut = pd.Timestamp("2023-09-24")  # test origins span the COVID period
    mask = [["2020-03-15", "2021-09-26"]]
    out.append(dict(name="real_wili_covid", seed=0, kind="proportion", scale=100, df=w[w.ds <= cut], mask=mask, origin_min=300))
    out.append(dict(name="real_num_ili_covid", seed=0, kind="count", df=c[c.ds <= cut], mask=mask, origin_min=300))
    return out


def _tf(kind: str, scale: float):
    from ailab.transforms import for_kind

    return for_kind(kind, scale)


def run_one(sc: dict, n_windows: int, masked: bool = False) -> dict:
    """Rolling-origin CV for one series (optionally with the mask option applied)."""
    from ailab import quality
    from ailab.schema import SeriesMeta

    df = sc["df"]
    meta = SeriesMeta(
        series_id="b",
        name="b",
        indicator_type=sc["kind"],
        value_scale=sc.get("scale", 1),
        season_length=SL,
        mask_periods=sc.get("mask", []) if masked else [],
    )
    g, mflags, _ = quality.model_frame(df, meta)
    tf = _tf(sc["kind"], sc.get("scale", 1))
    z = pd.DataFrame({"unique_id": "s", "ds": g["ds"], "y": tf.forward(g["y"].to_numpy())})
    n = len(g)
    nw = min(n_windows, (n - 3 * SL - H) // STEP + 1)
    t0 = time.time()
    cv = _sf(SL).cross_validation(df=z, h=H, step_size=STEP, n_windows=nw)
    cv = cv.reset_index() if "unique_id" not in cv.columns else cv
    cv["h"] = ((cv["ds"] - cv["cutoff"]).dt.days // 7).astype(int)
    for name, members in ENSEMBLES.items():
        cv[name] = cv[members].median(axis=1)
    truth_raw = quality.regularise(df).set_index("ds")["y"]  # observed (unmasked) truth
    cv["y_obs"] = truth_raw.reindex(cv["ds"]).to_numpy()
    cv["y_z"] = cv["y"]  # transformed truth as used for fitting (masked => filled)
    origin_idx = {d: i for i, d in enumerate(g["ds"])}
    cv["origin_i"] = cv["cutoff"].map(origin_idx)
    if sc.get("origin_min"):
        cv = cv[cv["origin_i"] >= sc["origin_min"]]
    # score only non-disrupted targets (same set for masked and unmasked variants)
    dis = np.zeros(len(truth_raw), bool)
    for a, b in sc.get("mask", []):
        dis |= ((truth_raw.index >= a) & (truth_raw.index <= b))
    bad = set(truth_raw.index[dis])
    cv = cv[~cv["ds"].isin(bad)].reset_index(drop=True)
    return {"cv": cv, "tf": tf, "seconds": time.time() - t0}


def score(cv: pd.DataFrame, tf, models=ALL) -> dict:
    """MAE/MASE-free metrics + out-of-sample interval coverage and WIS per model."""
    out: dict[str, dict] = {}
    cutoffs = np.sort(cv["cutoff"].unique())
    k = int(len(cutoffs) * 0.6)
    cal_c, test_c = cutoffs[:k], cutoffs[k + math.ceil(H / STEP) :]
    cal, test = cv[cv["cutoff"].isin(cal_c)], cv[cv["cutoff"].isin(test_c)]
    if len(test) == 0 or len(cal) == 0:
        return out
    for m in models:
        y_hat = tf.inverse(test[m].to_numpy())
        y = test["y_obs"].to_numpy()
        mae = float(np.mean(np.abs(y - y_hat)))
        cal_mae = float(np.mean(np.abs(cal["y_obs"].to_numpy() - tf.inverse(cal[m].to_numpy()))))
        res = {"mae": mae, "cal_mae": cal_mae, "n_test": int(len(test))}
        for variant in ("abs", "abs_mono", "signed", "abs_recent", "abs_cp"):
            cov, wis = _interval_scores(cal, test, m, tf, variant)
            res[variant] = {"cov80": cov[0.8], "cov95": cov[0.95], "wis": wis}
        out[m] = res
    return out


def _qhi(a: np.ndarray, level: float) -> float:
    n = len(a)
    k = min(n, math.ceil((n + 1) * level))
    return float(np.sort(a)[k - 1])


def _interval_scores(cal, test, m, tf, variant):
    """Intervals in transformed space from calibration errors; scored in original units."""
    widths: dict[tuple[int, float], tuple[float, float]] = {}
    for h in range(1, H + 1):
        e_all = (cal.loc[cal.h == h, "y_z"] - cal.loc[cal.h == h, m]).to_numpy()
        if variant == "abs_recent":  # use only the most recent 60 % of calibration origins
            e_all = e_all[-max(10, int(0.6 * len(e_all))) :]
        for lv in LEVELS:
            if variant == "signed":
                a = (1 - lv) / 2
                lo = float(np.quantile(e_all, a))
                hi = float(np.quantile(e_all, 1 - a))
                widths[(h, lv)] = (lo, hi)
            else:
                q = _qhi(np.abs(e_all), lv)
                widths[(h, lv)] = (-q, q)
    if variant == "abs_mono":  # widths must not shrink with the horizon
        for lv in LEVELS:
            run = 0.0
            for h in range(1, H + 1):
                run = max(run, widths[(h, lv)][1])
                widths[(h, lv)] = (-run, run)
    cover = {lv: [] for lv in LEVELS}
    wis_parts = []
    y_obs = test["y_obs"].to_numpy()
    zhat = test[m].to_numpy()
    hh = test["h"].to_numpy().astype(int)
    point = tf.inverse(zhat)
    wis = np.abs(y_obs - point) * 0.5
    wsum = 0.5
    for lv in LEVELS:
        a = 1 - lv
        lo_z = zhat + np.array([widths[(h, lv)][0] for h in hh])
        hi_z = zhat + np.array([widths[(h, lv)][1] for h in hh])
        lo, hi = tf.inverse(lo_z), tf.inverse(hi_z)
        cover[lv] = float(np.mean((y_obs >= lo) & (y_obs <= hi)))
        pen = (hi - lo) + (2 / a) * np.maximum(lo - y_obs, 0) + (2 / a) * np.maximum(y_obs - hi, 0)
        wis = wis + (a / 2) * pen
        wsum += a / 2
    wis = float(np.mean(wis / wsum))
    return {0.8: cover[0.8], 0.95: cover[0.95]}, wis


def _job(args):
    sc, nw, masked = args
    r = run_one(sc, nw, masked)
    return sc["name"], sc["seed"], masked, score(r["cv"], r["tf"]), r["seconds"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default="benchmarks/results/forecast.json")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    scs = scenarios(a.quick)
    nw = 40
    jobs = []
    for sc in scs:
        jobs.append((sc, nw, False))
        if sc.get("mask"):
            jobs.append((sc, nw, True))
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(_job, jobs))
    rows = [
        {"scenario": n, "seed": s, "masked": mk, "seconds": round(sec, 1), "metrics": m}
        for n, s, mk, m, sec in res
    ]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"rows": rows, "n_windows": nw, "h": H, "step": STEP}, indent=1))
    print(f"done: {len(rows)} runs in {time.time() - t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
