"""Parallel reads, parallel/simultaneous writes, idempotency under races, consistency."""

import collections
from concurrent.futures import ThreadPoolExecutor

from .conftest import body
from .harness import KEY, REC, Api


def run_parallel(base_url, fn, n, workers=16):
    def call(i):
        with Api(base_url) as c:
            return fn(c, i)

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(call, range(n)))


def test_parallel_reads(svc):
    import time

    t0 = time.perf_counter()
    res = run_parallel(
        svc.url,
        lambda c, i: (
            c.get(
                [
                    "/health",
                    "/ready",
                    "/api/v1/series",
                    "/api/v1/series/us_ilinet_wili",
                    "/api/v1/series/us_ilinet_wili/data?limit=50",
                ][i % 5]
            ).status_code
        ),
        200,
        workers=32,
    )
    REC.timings["200_parallel_reads_s"] = round(time.perf_counter() - t0, 2)
    assert set(res) == {200}


def test_parallel_writes_to_different_series(svc, api, uid):
    ids = [uid(f"w{i}_") for i in range(24)]
    res = run_parallel(
        svc.url,
        lambda c, i: (
            c.w(
                "POST", "/api/v1/series", json=body(ids[i], [float(i + j) for j in range(30)])
            ).status_code
        ),
        24,
    )
    assert res == [201] * 24
    for i, s in enumerate(ids):
        d = api.get(f"/api/v1/series/{s}/data").json()
        assert d["total"] == 30 and d["items"][0]["y"] == float(i)  # no cross-talk
    for s in ids:
        api.w("DELETE", f"/api/v1/series/{s}")


def test_simultaneous_create_same_id_without_overwrite_exactly_one_wins(svc, api, uid):
    s = uid()
    res = run_parallel(
        svc.url,
        lambda c, i: c.w("POST", "/api/v1/series", json=body(s, [float(i)] * 20)).status_code,
        16,
    )
    cnt = collections.Counter(res)
    assert cnt[201] == 1 and cnt[409] == 15 and set(cnt) <= {201, 409}, cnt
    d = api.get(f"/api/v1/series/{s}/data").json()
    assert d["total"] == 20 and len({o["y"] for o in d["items"]}) == 1
    api.w("DELETE", f"/api/v1/series/{s}")


def test_simultaneous_overwrites_leave_a_consistent_series(svc, api, uid):
    s = uid()
    assert api.w("POST", "/api/v1/series", json=body(s, [0.0] * 40)).status_code == 201
    res = run_parallel(
        svc.url,
        lambda c, i: (
            c.w(
                "POST",
                "/api/v1/series",
                params={"overwrite": "true"},
                json=body(s, [float(i + 1)] * (20 + i)),
            ).status_code
        ),
        16,
    )
    assert set(res) == {201}
    d = api.get(f"/api/v1/series/{s}/data", params={"limit": 5000}).json()
    vals = {o["y"] for o in d["items"]}
    assert len(vals) == 1, "mixed observations from different writers"
    assert d["total"] == 20 + int(vals.pop()) - 1  # length matches the surviving writer
    assert (
        len(
            [
                m
                for m in api.get("/api/v1/series", params={"limit": 200}).json()["items"]
                if m["series_id"] == s
            ]
        )
        == 1
    )
    api.w("DELETE", f"/api/v1/series/{s}")


def test_concurrent_delete_and_read(svc, api, mk):
    s = mk([float(i) for i in range(30)])

    def fn(c, i):
        if i == 5:
            return c.w("DELETE", f"/api/v1/series/{s}").status_code
        return c.get(f"/api/v1/series/{s}/data").status_code

    res = run_parallel(svc.url, fn, 40)
    assert set(res) <= {200, 404} and res[5] == 200


def test_parallel_derive_same_id_one_wins(svc, api, mk, uid):
    a = mk([1.0] * 20, tag="a")
    d = uid()
    res = run_parallel(
        svc.url,
        lambda c, i: (
            c.w(
                "POST", "/api/v1/derive", json={"series_id": d, "name": "d", "expr": f"{a} + {i}"}
            ).status_code
        ),
        12,
    )
    cnt = collections.Counter(res)
    assert cnt[201] == 1 and cnt[409] == 11, cnt
    api.w("DELETE", f"/api/v1/series/{d}")


def test_parallel_forecasts_same_series_are_consistent(svc, api, uid):
    from ailab.synth import synth_series

    s = uid()
    df = synth_series(52 * 4, "count", seed=3)
    assert (
        api.w("POST", "/api/v1/series", json=body(s, [float(v) for v in df["y"]])).status_code
        == 201
    )
    res = run_parallel(
        svc.url, lambda c, i: c.get(f"/api/v1/series/{s}/forecast").json(), 6, workers=6
    )
    assert all(r == res[0] for r in res)
    api.w("DELETE", f"/api/v1/series/{s}")


def test_writes_while_reading_lists(svc, api, uid):
    ids = [uid(f"l{i}_") for i in range(10)]

    def fn(c, i):
        if i % 2 == 0:
            return c.w("POST", "/api/v1/series", json=body(ids[i // 2], [1.0] * 10)).status_code
        r = c.get("/api/v1/series", params={"limit": 200})
        assert r.json()["total"] >= 2
        return r.status_code

    res = run_parallel(svc.url, fn, 20)
    assert set(res) <= {200, 201}
    for s in ids:
        api.w("DELETE", f"/api/v1/series/{s}")


def test_key_constant_exported():
    assert KEY
