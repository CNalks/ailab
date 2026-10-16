"""E2E fixtures. Run:  make e2e (local `ailab serve`)  |  make e2e-container (podman/docker image).

Everything is black-box over HTTP against a freshly started service with an empty database.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path

import pytest

from ailab.testset import ID_PREFIX, build_cases, load_into_service

from .harness import KEY, REC, TARGET, Api, Instance

HERE = Path(__file__).parent
CORS_ORIGIN = "https://app.example.test"


def pytest_collection_modifyitems(items):
    for it in items:
        if "tests/e2e" in str(it.fspath).replace("\\", "/") and not it.get_closest_marker("slow"):
            it.add_marker(pytest.mark.e2e)


@pytest.fixture(autouse=True)
def _track(request):
    REC.current = request.node.nodeid
    yield


@pytest.fixture(scope="session")
def workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("e2e")


@pytest.fixture(scope="session")
def svc(workdir):
    """Main service: write key set, demo preloaded, CORS for one origin, default limits."""
    inst = Instance(
        "main",
        {"AILAB_API_KEY": KEY, "AILAB_PRELOAD_DEMO": "1", "AILAB_CORS_ORIGINS": CORS_ORIGIN},
        workdir,
    )
    yield inst
    REC.notes["main_log_tail"] = inst.logs()[-3000:]
    inst.close()


@pytest.fixture(scope="session")
def api(svc):
    with Api(svc.url) as c:
        yield c


@pytest.fixture(scope="session")
def ro_svc(workdir):
    """Read-only service: no AILAB_API_KEY at all."""
    inst = Instance("ro", {"AILAB_PRELOAD_DEMO": "1", "AILAB_API_KEY": None}, workdir)
    yield inst
    inst.close()


@pytest.fixture(scope="session")
def ro(ro_svc):
    with Api(ro_svc.url, key=None) as c:
        yield c


@pytest.fixture(scope="session")
def lim_svc(workdir):
    """Tight limits to exercise 413 / series & observation caps cheaply."""
    inst = Instance(
        "lim",
        {
            "AILAB_API_KEY": KEY,
            "AILAB_MAX_SERIES": "4",
            "AILAB_MAX_OBSERVATIONS": "60",
            "AILAB_MAX_BODY_BYTES": "8192",
        },
        workdir,
    )
    yield inst
    inst.close()


@pytest.fixture(scope="session")
def lim(lim_svc):
    with Api(lim_svc.url) as c:
        yield c


@pytest.fixture(scope="session")
def cases():
    return {c.case_id: c for c in build_cases()}


@pytest.fixture(scope="session")
def loaded(api, cases):
    """The whole synthetic test set loaded once through the REST API (timed)."""
    t0 = time.perf_counter()
    res = load_into_service(api.base_url.__str__(), KEY, list(cases.values()))
    REC.timings["load_testset_s"] = round(time.perf_counter() - t0, 2)
    assert all(r["ok"] for r in res), [r for r in res if not r["ok"]]
    return res


def sid(case_id: str) -> str:
    return f"{ID_PREFIX}{case_id}"


@pytest.fixture
def uid(request):
    """Unique, valid series-id factory scoped to the test."""
    base = re.sub(r"[^a-z0-9]+", "_", request.node.name.lower()).strip("_")[:40]
    n = [0]

    def make(tag: str = "") -> str:
        n[0] += 1
        return f"u_{base}_{tag}{n[0]}_{os.urandom(2).hex()}"[:64]

    return make


def body(series_id: str, ys, kind="count", name="t", start="2020-01-05", **meta) -> dict:
    import datetime as dt

    d0 = dt.date.fromisoformat(start)
    return {
        "meta": {"series_id": series_id, "name": name, "indicator_type": kind, **meta},
        "observations": [
            {"ds": (d0 + dt.timedelta(weeks=i)).isoformat(), "y": y} for i, y in enumerate(ys)
        ],
    }


@pytest.fixture
def mk(api, uid):
    """Create series; deleted again at test end. mk(ys, kind=..., tag=...) -> series id."""
    made: list[str] = []

    def make(ys, kind="count", tag="", **meta):
        s = uid(tag)
        r = api.w("POST", "/api/v1/series", json=body(s, ys, kind, **meta))
        assert r.status_code == 201, r.text
        made.append(s)
        return s

    yield make
    for s in reversed(made):
        api.w("DELETE", f"/api/v1/series/{s}", params={"cascade": "true"})


# ---- result collection for docs/TEST_REPORT.md -------------------------------------------
_OUT: dict[str, str] = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    out = yield
    rep = out.get_result()
    if "tests/e2e" in str(item.fspath).replace("\\", "/") and rep.when in ("setup", "call"):
        if rep.when == "call" or rep.outcome != "passed":
            _OUT[item.nodeid] = rep.outcome
            REC.notes.setdefault("durations", {})[item.nodeid] = round(rep.duration, 3)


def pytest_sessionfinish(session, exitstatus):
    if not _OUT:
        return
    per_test = defaultdict(set)
    for node, route, _status in REC.hits:
        per_test[node].add(route)
    doc = {
        "target": TARGET,
        "outcomes": _OUT,
        "routes": {k: sorted(v) for k, v in per_test.items()},
        "timings": REC.timings,
        "durations": REC.notes.get("durations", {}),
        "requests": len(REC.hits),
    }
    out = Path(os.environ.get("AILAB_E2E_RESULTS", f"out/e2e-{TARGET}.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1))
