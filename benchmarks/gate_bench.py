"""Gate validation: false rejection of good series vs refusal of bad ones (known ground truth).

Run: uv run python benchmarks/gate_bench.py [--seeds 8] [--full-seeds 3] [--out benchmarks/results/gate.json]

* GOOD series (the gate must not refuse; ``limited``/warnings are tolerated but counted):
  seasonal epidemics (6-8 seasons, several amplitudes/sharpness, low count, proportion), seasonal
  with trend, with 3 % random gaps, overdispersed, the real FluView fixture (wILI, num_ILI).
* BAD series (the gate must refuse): too short, 40 % gaps, constant, mostly zeros, 30-week trailing gap.
* HARD series (no right answer: expect a warning or limited mode, a refusal is not required): pure
  random walk, white noise without seasonality, 3x level shift in the last 80 weeks.
Stage 1 (data checks) runs on every seed; the full pipeline (stage 1 + rolling-origin backtest) on
``--full-seeds`` seeds because the backtest costs seconds per series.
"""

from __future__ import annotations

import argparse
import json
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from ailab.pipeline import analyse_frame, gate_data_only  # noqa: E402
from ailab.schema import SeriesMeta  # noqa: E402
from ailab.sources import fluview  # noqa: E402
from ailab.synth import START, synth_series  # noqa: E402


def meta(kind="count", **kw) -> SeriesMeta:
    return SeriesMeta(series_id="b", name="b", indicator_type=kind, season_length=52, **kw)


def S(seed, **kw):
    return synth_series(n_weeks=kw.pop("n_weeks", 52 * 7), seed=seed, **kw)


def walk(seed, n=52 * 7):
    rng = np.random.default_rng(seed)
    y = np.maximum(100 + np.cumsum(rng.normal(0, 8, n)), 1).round()
    return pd.DataFrame({"ds": pd.date_range(START, periods=n, freq="7D"), "y": y})


def noise(seed, n=52 * 7):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {"ds": pd.date_range(START, periods=n, freq="7D"), "y": rng.poisson(rng.gamma(0.7, 40, n))}
    )


def zeros(seed, n=52 * 7):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {"ds": pd.date_range(START, periods=n, freq="7D"), "y": rng.binomial(1, 0.12, n) * 3.0}
    )


def trailing_gap(seed):
    d = S(seed)
    d.loc[d.index[-30:], "y"] = np.nan  # the feed went silent 30 weeks ago
    return d


def level_shift(seed):
    d = S(seed, level=100)
    d.loc[d.index >= len(d) - 80, "y"] = (d.loc[d.index >= len(d) - 80, "y"] * 3).round()
    return d


def const(seed):
    d = S(seed)
    d["y"] = 50.0
    return d


def wili(seed):
    raw = fluview.fixture_dataframe()
    return fluview.to_series(raw, "wili")


def num_ili(seed):
    return fluview.to_series(fluview.fixture_dataframe(), "num_ili")


GOOD = {
    "seasonal": (lambda s: S(s), meta()),
    "seasonal_sharp": (lambda s: S(s, sharpness=4.0, amplitude=8.0), meta()),
    "seasonal_4_seasons": (lambda s: S(s, n_weeks=52 * 4), meta()),
    "seasonal_trend": (lambda s: S(s, trend=0.002), meta()),
    "seasonal_lowcount": (lambda s: S(s, level=8.0), meta()),
    "seasonal_gaps3pct": (lambda s: S(s, missing_frac=0.03), meta()),
    "seasonal_overdispersed": (lambda s: S(s, dispersion=0.3), meta()),
    "seasonal_prop": (
        lambda s: S(s, kind="proportion", value_scale=100),
        meta("proportion", value_scale=100),
    ),
    "real_wili": (wili, meta("proportion", value_scale=100)),
    "real_num_ili": (num_ili, meta()),
}
HARD = {  # no ground-truth "refuse": the honest outcome is a warning/limit (or a baseline forecast)
    "random_walk": (walk, meta()),
    "white_noise_no_season": (noise, meta()),
    "level_shift_x3": (level_shift, meta()),
}
BAD = {
    "too_short_30w": (lambda s: S(s, n_weeks=30), meta()),
    "short_1.5_seasons": (lambda s: S(s, n_weeks=78), meta()),
    "gaps_40pct": (lambda s: S(s, missing_frac=0.4), meta()),
    "constant": (const, meta()),
    "mostly_zeros": (zeros, meta()),
    "trailing_gap_30w": (trailing_gap, meta()),
}


def run(seeds: int, full_seeds: int) -> dict:
    out = {"stage1": {}, "full": {}}
    for kind, table in (("good", GOOD), ("bad", BAD), ("hard", HARD)):
        for name, (mk, m) in table.items():
            res, codes = Counter(), Counter()
            n = 1 if name.startswith("real_") else seeds
            for s in range(n):
                d = gate_data_only(m, mk(s)).to_dict()
                res[d["decision"]] += 1
                codes.update(d["reason_codes"])
            out["stage1"][name] = {
                "truth": kind,
                "n": n,
                "decision": dict(res),
                "codes": dict(codes),
            }
    for kind, table in (("good", GOOD), ("bad", BAD), ("hard", HARD)):
        for name, (mk, m) in table.items():
            res, codes = Counter(), Counter()
            n = 1 if name.startswith("real_") else full_seeds
            t0 = time.time()
            for s in range(n):
                r = analyse_frame(m, mk(s), horizon=8)
                res[r["gate"]["decision"]] += 1
                codes.update(r["gate"]["reason_codes"])
            out["full"][name] = {
                "truth": kind,
                "n": n,
                "decision": dict(res),
                "codes": dict(codes),
                "sec_per_series": round((time.time() - t0) / n, 1),
            }
            print(name, dict(res), flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--full-seeds", type=int, default=3)
    ap.add_argument("--out", default="benchmarks/results/gate.json")
    a = ap.parse_args()
    res = run(a.seeds, a.full_seeds)
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    for stage in ("stage1", "full"):
        print(
            f"\n### {stage}\n\n| scenario | truth | n | allowed | limited | refused | codes |\n|---|---|---|---|---|---|---|"
        )
        for k, v in res[stage].items():
            d = v["decision"]
            c = ", ".join(
                f"{c}×{n}" for c, n in sorted(v["codes"].items(), key=lambda x: -x[1])[:4]
            )
            print(
                f"| {k} | {v['truth']} | {v['n']} | {d.get('allowed', 0)} | {d.get('limited', 0)} | {d.get('refused', 0)} | {c} |"
            )


if __name__ == "__main__":
    main()
