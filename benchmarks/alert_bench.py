"""Alert benchmark on synthetic seasonal count series with injected outbreaks of known onset.

Run: uv run python benchmarks/alert_bench.py [--quick] [--out benchmarks/results/alerts.json]

Truth: each outbreak starts at a known week t0 and lasts D=8 weeks with a multiplicative excess
``1 + A sin(pi (w+1)/(D+1))`` (A = peak excess: 0.5 / 1 / 2 = +50 % / +100 % / +200 %).
Series: 8 seasons of seasonal gamma-Poisson counts (flu-like profile), level 20 or 150, two
contaminating outbreaks in the first 3 seasons (so baselines are not clean), six outbreaks in the
evaluation region (weeks >= 156). A *false alarm* is an alarm in a week that is neither inside an
outbreak nor in the 2 weeks after it. POD = share of outbreaks with >= 1 alarm during the outbreak;
timeliness = first alarm week - t0 (weeks, detected outbreaks only).
Thresholds are compared at a matched false-alarm rate (<= 2 per 100 clean weeks).
"""

from __future__ import annotations

import argparse
import json
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
N = 52 * 8
D = 8
EVAL_FROM = 156


def make(
    seed: int, level: float, amp: float, variant: str = "base"
) -> tuple[pd.DataFrame, list[int]]:
    from ailab.synth import _seasonal

    rng = np.random.default_rng(seed * 1000 + int(level))
    season = _seasonal(N, 52, 2.0)
    mu = level * (1 + (0.0 if variant == "flat" else 3.0) * season)
    if variant == "trend":
        mu = mu * (1 + 1.0 * np.arange(N) / N)
    disp = 0.25 if variant == "overdispersed" else 0.05
    mu = mu * rng.gamma(1 / disp, disp, N)
    onsets = [60 + int(rng.integers(0, 10)), 110 + int(rng.integers(0, 10))]
    ev = []
    t = EVAL_FROM + int(rng.integers(5, 20))
    while t < N - D - 2 and len(ev) < 6:
        ev.append(t)
        t += int(rng.integers(30, 45))
    y_mu = mu.copy()
    for t0 in onsets + ev:
        a = 2.0 if t0 < EVAL_FROM else amp
        for w in range(D):
            y_mu[t0 + w] *= 1 + a * np.sin(np.pi * (w + 1) / (D + 1))
    y = rng.poisson(y_mu).astype(float)
    return pd.DataFrame({"ds": pd.date_range("2012-01-01", periods=N, freq="7D"), "y": y}), ev


CONFIGS: dict[str, dict] = {}
for b in (7, 10, 14, 21):
    CONFIGS[f"ears_c1_b{b}"] = dict(method="ears_c1", baseline=b)
    CONFIGS[f"ears_c2_b{b}"] = dict(method="ears_c2", baseline=b)
    CONFIGS[f"ears_c3_b{b}"] = dict(method="ears_c3", baseline=b)
    CONFIGS[f"cusum_b{b}"] = dict(method="cusum", baseline=b)
CONFIGS["farrington_b5w3"] = dict(method="farrington", baseline=7)
THR = {
    "ears_c1": np.round(np.arange(1.5, 8.01, 0.25), 2),
    "ears_c2": np.round(np.arange(1.5, 8.01, 0.25), 2),
    "ears_c3": np.round(np.arange(0.5, 12.01, 0.5), 2),
    "cusum": np.round(np.arange(1.0, 14.01, 0.5), 2),
    "farrington": np.round(np.arange(1.0, 6.01, 0.25), 2),
}


def _job(args):
    from ailab import alerts

    seed, level, amp, variant = args
    df, ev = make(seed, level, amp, variant)
    mask_out = np.zeros(N, bool)  # in outbreak or 2 weeks after
    for t0 in ev:
        mask_out[t0 : t0 + D + 2] = True
    res = {}
    for name, c in CONFIGS.items():
        m = c["method"]
        if m == "cusum":
            for thr in THR[m]:
                r = alerts.detect(df, m, c["baseline"], float(thr))
                res[(name, float(thr))] = _score(
                    r["alarm"].to_numpy(), r["score"].to_numpy(), ev, mask_out
                )
        else:
            r = alerts.detect(df, m, c["baseline"], 1e9)
            sc = r["score"].to_numpy()
            for thr in THR[m]:
                alarm = np.where(np.isnan(sc), False, sc > thr)
                res[(name, float(thr))] = _score(alarm, sc, ev, mask_out)
    return seed, level, amp, variant, res


def _score(alarm, score, ev, mask_out):
    ok = ~np.isnan(score)
    clean = ok & ~mask_out
    clean[:EVAL_FROM] = False
    n_clean = int(clean.sum())
    fa = int((alarm & clean).sum())
    det, lat = 0, []
    for t0 in ev:
        w = alarm[t0 : t0 + D]
        if w.any():
            det += 1
            lat.append(int(np.argmax(w)))
    return {
        "n_clean": n_clean,
        "false_alarms": fa,
        "detected": det,
        "n_outbreaks": len(ev),
        "lat": lat,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default="benchmarks/results/alerts.json")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--variant", default="base", choices=["base", "flat", "overdispersed", "trend"])
    a = ap.parse_args()
    seeds = range(4 if a.quick else 20)
    jobs = [(s, L, A, a.variant) for s in seeds for L in (20, 150) for A in (0.5, 1.0, 2.0)]
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex:
        out = list(ex.map(_job, jobs))
    agg: dict = {}
    for _seed, level, amp, _v, res in out:
        for (name, thr), r in res.items():
            d = (
                agg.setdefault(name, {})
                .setdefault(str(thr), {})
                .setdefault(f"L{int(level)}_A{amp}", [])
            )
            d.append(r)
    # reduce
    red = {}
    for name, by_thr in agg.items():
        red[name] = {}
        for thr, by_sc in by_thr.items():
            cell = {}
            for sc, lst in by_sc.items():
                cell[sc] = {
                    "n_clean": sum(x["n_clean"] for x in lst),
                    "false_alarms": sum(x["false_alarms"] for x in lst),
                    "detected": sum(x["detected"] for x in lst),
                    "n_outbreaks": sum(x["n_outbreaks"] for x in lst),
                    "lat": [v for x in lst for v in x["lat"]],
                }
            red[name][thr] = cell
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(
        json.dumps({"results": red, "seeds": len(list(seeds)), "variant": a.variant})
    )
    print(f"done in {time.time() - t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
