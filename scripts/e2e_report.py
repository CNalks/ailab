"""Summarise out/e2e-*.json (written by tests/e2e) as markdown: results, endpoint x scenario
coverage and timings.  Usage: python scripts/e2e_report.py [out]"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "out")
runs = {
    p.stem.removeprefix("e2e-"): json.loads(p.read_text()) for p in sorted(out.glob("e2e-*.json"))
}
if not runs:
    sys.exit("no out/e2e-*.json found; run `make e2e` first")

OPS = [
    "GET /health",
    "GET /ready",
    "GET /docs",
    "GET /openapi.json",
    "GET /api/v1/series",
    "POST /api/v1/series",
    "GET /api/v1/series/{id}",
    "DELETE /api/v1/series/{id}",
    "GET /api/v1/series/{id}/data",
    "GET /api/v1/series/{id}/observations",
    "GET /api/v1/series/{id}/quality",
    "GET /api/v1/series/{id}/lineage",
    "GET /api/v1/series/{id}/forecast",
    "GET /api/v1/series/{id}/policy",
    "GET /api/v1/series/{id}/gate",
    "GET /api/v1/series/{id}/alerts",
    "GET /api/v1/series/{id}/export",
    "POST /api/v1/series/{id}/recompute",
    "POST /api/v1/derive",
    "POST /api/v1/derive/preview",
    "GET /api/v1/alerts/rules",
    "PUT /api/v1/alerts/rules",
    "DELETE /api/v1/alerts/rules/{rule_id}",
    "GET /api/v1/alerts/evaluate",
    "POST /api/v1/export",
]


def norm(route: str) -> str:
    """Hostile paths (injection ids, odd methods) are folded into one 'other' row."""
    return route if route in OPS else "other (hostile/unknown paths, odd methods)"


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
    "test_docs_samples": "docs samples",
}
for name, d in runs.items():
    o = d["outcomes"]
    c = defaultdict(int)
    for v in o.values():
        c[v] += 1
    print(
        f"## {name}: {len(o)} tests, passed {c['passed']}, failed {c['failed']}, "
        f"skipped {c['skipped']}, errors {len(o) - sum(c.values())}; {d['requests']} HTTP requests\n"
    )
    cov = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # route -> scen -> [passed, total]
    for node, routes in d["routes"].items():
        mod = re.search(r"(test_\w+)\.py", node).group(1)
        if mod == "test_docs_samples":
            continue  # samples run through curl/python subprocesses, not the recording client
        scen = SCEN.get(mod, "other")
        for r in {norm(x) for x in routes}:
            cell = cov[r][scen]
            cell[1] += 1
            cell[0] += o.get(node) == "passed"
    scens = [s for s in SCEN.values() if any(s in v for v in cov.values())]
    print("| endpoint | " + " | ".join(scens) + " |")
    print("|---|" + "---|" * len(scens))
    for r in [*OPS, norm("x")]:
        if r not in cov:
            print(f"| `{r}` | " + " | ".join("**not covered**" for _ in scens) + " |")
            continue
        cells = [f"{cov[r][s][0]}/{cov[r][s][1]}" if s in cov[r] else "·" for s in scens]
        print(f"| `{r}` | " + " | ".join(cells) + " |")
    print("\ntimings:", json.dumps(d["timings"], ensure_ascii=False), "\n")
