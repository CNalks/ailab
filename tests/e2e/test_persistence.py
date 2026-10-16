"""Restart / crash persistence (volume-backed DuckDB), demo preload idempotency."""

import time

import pytest

from .conftest import body
from .harness import KEY, REC, TARGET, Api, Instance


@pytest.fixture(scope="module")
def inst(workdir):
    i = Instance("persist", {"AILAB_API_KEY": KEY, "AILAB_PRELOAD_DEMO": "1"}, workdir)
    yield i
    i.close()


def snapshot(c):
    items = c.get("/api/v1/series", params={"limit": 200}).json()["items"]
    out = {}
    for m in items:
        d = c.get(f"/api/v1/series/{m['series_id']}/data", params={"limit": 5000}).json()
        out[m["series_id"]] = (m["derived_expr"], [o["y"] for o in d["items"]])
    return out, c.get("/api/v1/alerts/rules").json()


def test_data_rules_and_lineage_survive_graceful_restart(inst):
    with Api(inst.url) as c:
        ys = [1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        assert (
            c.w("POST", "/api/v1/series", json=body("p_num", ys, name="流感 ✓")).status_code == 201
        )
        assert c.w("POST", "/api/v1/series", json=body("p_den", [10.0] * 8)).status_code == 201
        r = c.w(
            "POST",
            "/api/v1/derive",
            json={"series_id": "p_ratio", "name": "r", "expr": "p_num / p_den"},
        )
        assert r.status_code == 201
        assert (
            c.w(
                "PUT",
                "/api/v1/alerts/rules",
                json={"rule_id": "p_rule", "series_id": "p_num", "method": "cusum", "baseline": 5},
            ).status_code
            == 200
        )
        before = snapshot(c)
        assert "us_ilinet_wili" in before[0]
    t0 = time.perf_counter()
    inst.restart()
    REC.timings["restart_to_ready_s"] = round(time.perf_counter() - t0, 1)
    with Api(inst.url) as c:
        after = snapshot(c)
        assert after == before
        lin = c.get("/api/v1/series/p_ratio/lineage").json()
        assert lin["derived_from"] == ["p_den", "p_num"] and lin["is_derived"]
        assert c.get("/api/v1/series/p_num").json()["name"] == "流感 ✓"
        assert c.get("/api/v1/series", params={"limit": 200}).json()["total"] == len(
            before[0]
        )  # demo not re-added
        # still writable with the same key, and lineage-based cascade still works
        d = c.w("DELETE", "/api/v1/series/p_num", params={"cascade": "true"})
        assert set(d.json()["deleted"]) == {"p_num", "p_ratio"}


def test_deletions_survive_restart_and_demo_is_not_resurrected(inst):
    with Api(inst.url) as c:
        assert c.w("DELETE", "/api/v1/series/us_ilinet_ili_visits").status_code in (200, 409)
        c.w("DELETE", "/api/v1/series/us_ilinet_ili_visits", params={"cascade": "true"})
        c.w("DELETE", "/api/v1/series/p_den")
        n = c.get("/api/v1/series").json()["total"]
    inst.restart()
    with Api(inst.url) as c:
        assert c.get("/api/v1/series").json()["total"] == n
        assert c.get("/api/v1/series/us_ilinet_ili_visits").status_code == 404


def test_crash_kill_9_keeps_committed_writes(inst):
    with Api(inst.url) as c:
        for i in range(5):
            assert (
                c.w(
                    "POST",
                    "/api/v1/series",
                    params={"overwrite": "true"},
                    json=body(f"crash_{i}", [float(i)] * 30),
                ).status_code
                == 201
            )
        before = snapshot(c)
    inst.restart(hard=True)
    with Api(inst.url) as c:
        assert snapshot(c) == before
        assert c.get("/health").json()["status"] == "ok"


@pytest.mark.skipif(TARGET != "container", reason="container-only")
def test_container_recreate_keeps_volume_data(inst):
    with Api(inst.url) as c:
        assert (
            c.w(
                "POST",
                "/api/v1/series",
                params={"overwrite": "true"},
                json=body("vol_check", [1.0, 2.0, 3.0, 4.0]),
            ).status_code
            == 201
        )
    inst.recreate()  # new container, same named volume
    with Api(inst.url) as c:
        assert c.get("/api/v1/series/vol_check/data").json()["total"] == 4


def test_second_process_on_same_db_fails_cleanly_not_corrupting(inst, workdir):
    """DuckDB is single-process: a second server on the same file must refuse, not corrupt."""
    if TARGET == "container":
        pytest.skip("local-only (needs the same file path)")
    import os
    import subprocess
    import sys

    env = {**os.environ, "AILAB_DB": str(inst.workdir / "db.duckdb"), "AILAB_API_KEY": KEY}
    p = subprocess.run(
        [sys.executable, "-m", "ailab.cli", "serve", "--port", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert p.returncode != 0
    with Api(inst.url) as c:
        assert c.get("/health").json()["status"] == "ok"
