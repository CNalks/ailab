"""Summarise out/e2e-*.json (written by tests/e2e) as markdown: results, endpoint x scenario
coverage and timings.  Usage: python scripts/e2e_report.py [out]"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
runs = {p.stem.removeprefix("e2e-"): json.loads(p.read_text()) for p in sorted(out.glob("e2e-*.json"))}
if not runs:
    sys.exit("no out/e2e-*.json found; run `make e2e` first")

SCEN = {
    "test_system": "system",
    "test_auth": "auth",
    "test_series": "series CRUD/validation",
    "test_testset": "synthetic test set",
    "test_derive": "derive/safety",
    "test_alerts": "alerts",
    "test_forecast": "forecast/perf",
    "test_export": "export",
    "test_robustness": "limits/CORS/injection",
    "test_concurrency": "concurrency",
    "test_persistence": "persistence",
    "test_config": "config/hardening",
}
for name, d in runs.items():
    o = d["outcomes"]
    c = defaultdict(int)
    for v in o.values():
        c[v] += 1
    print(f"## {name}: {len(o)} tests, passed {c['passed']}, failed {c['failed']}, "
          f"skipped {c['skipped']}, errors {len(o) - sum(c.values())}; {d['requests']} HTTP requests\n")
    cov = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # route -> scen -> [passed, total]
    for node, routes in d["routes"].items():
        scen = SCEN.get(re.search(r"(test_\w+)\.py", node).group(1), "other")
        for r in routes:
            cell = cov[r][scen]
            cell[1] += 1
            cell[0] += o.get(node) == "passed"
    scens = [s for s in SCEN.values() if any(s in v for v in cov.values())]
    print("| endpoint | " + " | ".join(scens) + " |")
    print("|---|" + "---|" * len(scens))
    for r in sorted(cov, key=lambda x: (x.split(" ", 1)[1], x)):
        cells = [f"{cov[r][s][0]}/{cov[r][s][1]}" if s in cov[r] else "·" for s in scens]
        print(f"| `{r}` | " + " | ".join(cells) + " |")
    print("\ntimings:", json.dumps(d["timings"], ensure_ascii=False), "\n")
