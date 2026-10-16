"""Aggregate benchmarks/results/forecast.json into markdown tables (relative MAE, coverage, WIS)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

path = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmarks/results/forecast.json")
rows = json.loads(path.read_text())["rows"]


def gmean(x):
    x = [v for v in x if v and v > 0]
    return float(np.exp(np.mean(np.log(x)))) if x else float("nan")


def table(select, metric="rel_mae", ref="SeasonalNaive", variant="abs"):
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if not select(r) or not r["metrics"]:
            continue
        key = r["scenario"] + ("+mask" if r["masked"] else "")
        for m, v in r["metrics"].items():
            if metric == "rel_mae":
                by[key][m].append(v["mae"] / r["metrics"][ref]["mae"])
            elif metric == "rel_wis":
                by[key][m].append(v[variant]["wis"] / r["metrics"][ref][variant]["wis"])
            elif metric in ("cov80", "cov95"):
                by[key][m].append(v[variant][metric])
    return by


def md(by, fmt="{:.2f}", agg=gmean, models=None):
    models = models or list(next(iter(by.values())).keys())
    lines = ["| scenario | " + " | ".join(models) + " |", "|---|" + "---|" * len(models)]
    for k, d in by.items():
        lines.append("| " + k + " | " + " | ".join(fmt.format(agg(d[m])) for m in models) + " |")
    allv = {m: [x for d in by.values() for x in d[m]] for m in models}
    lines.append("| **all** | " + " | ".join("**" + fmt.format(agg(allv[m])) + "**" for m in models) + " |")
    return "\n".join(lines)


no_mask = lambda r: not r["masked"]  # noqa: E731
print("### relative MAE vs SeasonalNaive (geometric mean over seeds; <1 better)\n")
print(md(table(no_mask)))
print("\n### relative WIS vs SeasonalNaive (abs-error conformal, out-of-sample)\n")
print(md(table(no_mask, "rel_wis")))
for v in ("abs", "abs_mono", "signed", "abs_recent"):
    print(f"\n### out-of-sample 80% / 95% coverage, interval variant `{v}` (mean over series)\n")
    c80 = md(table(no_mask, "cov80", variant=v), "{:.2f}", np.mean, ["MSTL", "AutoTheta", "ENS_mstl_theta", "ENS_mstl_theta_swa"])
    c95 = md(table(no_mask, "cov95", variant=v), "{:.2f}", np.mean, ["MSTL", "AutoTheta", "ENS_mstl_theta", "ENS_mstl_theta_swa"])
    print("80%:\n" + c80 + "\n\n95%:\n" + c95)
print("\n### mask option: relative MAE on non-disrupted targets (same targets with/without mask)\n")
by = defaultdict(lambda: defaultdict(list))
for r in rows:
    if r["scenario"].startswith(("covid", "real_wili_covid", "real_num_ili_covid")) and r["metrics"]:
        key = r["scenario"] + ("+mask" if r["masked"] else "")
        for m in ("SeasonalNaive", "MSTL", "AutoTheta", "ENS_mstl_theta", "SWA3"):
            by[key][m].append(r["metrics"][m]["mae"])
print(md(by, "{:.3f}", np.mean, ["SeasonalNaive", "SWA3", "MSTL", "AutoTheta", "ENS_mstl_theta"]))

print("\n### model selection on the calibration part, scored on the held-out part (rel MAE vs SeasonalNaive)\n")
STRATS = {
    "fixed MSTL": None,
    "select {MSTL,Theta,Naive} (v0.2 rule)": ["MSTL", "AutoTheta", "Naive"],
    "select {MSTL,Theta,Naive,SWA3}": ["MSTL", "AutoTheta", "Naive", "SWA3"],
    "select {MSTL,Theta,Naive,ENS_mstl_theta}": ["MSTL", "AutoTheta", "Naive", "ENS_mstl_theta"],
    "select {MSTL,Theta,Naive,SWA3,ENS_mstl_theta_swa}": ["MSTL", "AutoTheta", "Naive", "SWA3", "ENS_mstl_theta_swa"],
    "fixed ENS_mstl_theta": "ENS_mstl_theta",
}
by = defaultdict(lambda: defaultdict(list))
for r in rows:
    if r["masked"] or not r["metrics"]:
        continue
    m = r["metrics"]
    for name, cands in STRATS.items():
        if cands is None:
            pick = "MSTL"
        elif isinstance(cands, str):
            pick = cands
        else:
            pick = min(cands, key=lambda c: m[c]["cal_mae"])
        by[r["scenario"]][name].append(m[pick]["mae"] / m["SeasonalNaive"]["mae"])
print(md(by, "{:.2f}", gmean, list(STRATS)))
