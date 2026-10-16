"""Summarise benchmarks/results/alerts.json: matched-false-alarm comparison and current defaults."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

path = Path(sys.argv[1] if len(sys.argv) > 1 else "benchmarks/results/alerts.json")
R = json.loads(path.read_text())["results"]
TARGET = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0  # false alarms per 100 clean weeks
DEFAULTS = {"ears_c1_b7": 3.0, "ears_c2_b7": 3.0, "ears_c3_b7": 2.0, "cusum_b7": 4.0, "farrington_b5w3": 2.0}


def pooled(cell, keys):
    n = sum(cell[k]["n_clean"] for k in keys)
    fa = sum(cell[k]["false_alarms"] for k in keys)
    det = sum(cell[k]["detected"] for k in keys)
    nb = sum(cell[k]["n_outbreaks"] for k in keys)
    lat = [v for k in keys for v in cell[k]["lat"]]
    return n, fa, det, nb, lat


def row(name, thr, cell):
    ks = list(cell)
    n, fa, _, _, _ = pooled(cell, ks)
    far = 100 * fa / max(n, 1)
    pods = []
    for A in ("0.5", "1.0", "2.0"):
        k = [x for x in ks if x.endswith(f"_A{A}")]
        _, _, d, nb, _ = pooled(cell, k)
        pods.append(d / max(nb, 1))
    _, _, d, nb, lat = pooled(cell, ks)
    low = [x for x in ks if x.startswith("L20_")]
    high = [x for x in ks if x.startswith("L150_")]
    pl = pooled(cell, low)
    ph = pooled(cell, high)
    return (
        f"| {name} | {thr} | {far:.2f} | " + " | ".join(f"{p:.0%}" for p in pods)
        + f" | {np.median(lat) if lat else float('nan'):.1f} | {pl[2] / max(pl[3], 1):.0%} / {ph[2] / max(ph[3], 1):.0%} |"
    )


hdr = "| method | threshold | false alarms /100 clean wk | POD A=+50% | A=+100% | A=+200% | median delay (wk) | POD low/high level |\n|---|---|---|---|---|---|---|---|"
print(f"### Current defaults (v0.2 thresholds)\n\n{hdr}")
for name, thr in DEFAULTS.items():
    print(row(name, thr, R[name][str(float(thr))]))
print(f"\n### Matched false-alarm rate (<= {TARGET}/100 clean weeks): smallest threshold meeting it\n\n{hdr}")
best = {}
for name, by_thr in R.items():
    for thr in sorted(by_thr, key=float):
        cell = by_thr[thr]
        n, fa, *_ = pooled(cell, list(cell))
        if 100 * fa / max(n, 1) <= TARGET:
            print(row(name, thr, cell))
            best[name] = thr
            break
    else:
        print(f"| {name} | (none) | | | | | | |")
