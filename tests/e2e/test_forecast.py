"""Forecast/policy endpoints: parameters, caching, invalidation, kind rules, responsiveness."""

import threading
import time

import pytest

from ailab.synth import synth_series

from .conftest import body, sid
from .harness import REC


def _series(api, s, df, kind="count", **meta):
    ys = [None if v != v else float(v) for v in df["y"]]
    r = api.w("POST", "/api/v1/series", json=body(s, ys, kind, **meta))
    assert r.status_code == 201, r.text


@pytest.fixture
def seasonal(api, uid):
    s = uid()
    _series(api, s, synth_series(52 * 6, "count", seed=21))
    yield s
    api.w("DELETE", f"/api/v1/series/{s}")


def test_forecast_demo_series_shape(api):
    r = api.get("/api/v1/series/us_ilinet_wili/forecast", params={"horizon": 4})
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {
        "series",
        "data_through",
        "quality",
        "policy",
        "backtest",
        "forecast",
        "schema_version",
    }
    p = j["policy"]
    if p["forecast_allowed"]:
        assert len(j["forecast"]["points"]) == p["horizon_weeks"] <= 4
    else:
        assert j["forecast"] is None and p["reason"]


@pytest.mark.parametrize("h", [1, 8, 26])
def test_horizon_bounds_ok(api, seasonal, h):
    r = api.get(f"/api/v1/series/{seasonal}/forecast", params={"horizon": h})
    assert r.status_code == 200
    p = r.json()["policy"]
    assert p["horizon_weeks"] <= h


@pytest.mark.parametrize("h", [0, -1, 27, 1000, "x", "1.5", ""])
def test_horizon_bounds_invalid(api, seasonal, h):
    for ep in ("forecast", "policy"):
        r = api.get(f"/api/v1/series/{seasonal}/{ep}", params={"horizon": h})
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_cache_hit_and_invalidation_on_overwrite(api, seasonal):
    t0 = time.perf_counter()
    a = api.get(f"/api/v1/series/{seasonal}/forecast").json()
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    b = api.get(f"/api/v1/series/{seasonal}/forecast").json()
    warm = time.perf_counter() - t0
    assert a == b
    REC.timings["forecast_6y_weekly_cold_s"] = round(cold, 2)
    REC.timings["forecast_6y_weekly_warm_s"] = round(warm, 3)
    assert warm < max(0.5, cold / 3)
    # new data => new analysis (data hash is part of the cache key)
    df = synth_series(52 * 6, "count", seed=99, level=500.0)
    _series_over = body(seasonal, [float(v) for v in df["y"]])
    assert (
        api.w("POST", "/api/v1/series", params={"overwrite": "true"}, json=_series_over).status_code
        == 201
    )
    c = api.get(f"/api/v1/series/{seasonal}/forecast").json()
    assert c["data_through"] == a["data_through"] and c["backtest"] != a["backtest"]


def test_forecast_on_deleted_series_is_404(api, mk):
    s = mk([1.0] * 5)
    api.w("DELETE", f"/api/v1/series/{s}")
    assert api.get(f"/api/v1/series/{s}/forecast").status_code == 404


def test_proportion_percent_forecast_stays_in_range(api, loaded):
    f = api.get(f"/api/v1/series/{sid('prop_percent')}/forecast").json()
    assert f["policy"]["indicator_type"] == "proportion"
    for pt in (f["forecast"] or {"points": []})["points"]:
        assert 0 <= pt["point"] <= 100


def test_health_stays_responsive_during_forecast(api, seasonal, uid):
    """A long analysis must not block /health or reads (compute is serialised, I/O is not)."""
    s2 = uid()
    _series(api, s2, synth_series(52 * 10, "count", seed=5))
    done = {}

    def run():
        t0 = time.perf_counter()
        done["r"] = api.get(f"/api/v1/series/{s2}/forecast", params={"horizon": 12})
        done["t"] = time.perf_counter() - t0

    th = threading.Thread(target=run)
    th.start()
    lat = []
    while th.is_alive():
        t0 = time.perf_counter()
        assert api.get("/health").status_code == 200
        lat.append(time.perf_counter() - t0)
        time.sleep(0.05)
    th.join()
    assert done["r"].status_code == 200
    REC.timings["health_max_latency_during_forecast_s"] = round(max(lat), 3)
    assert max(lat) < 2.0, f"/health blocked for {max(lat):.1f}s during a forecast"
    api.w("DELETE", f"/api/v1/series/{s2}")


@pytest.mark.slow
@pytest.mark.parametrize("weeks", [520, 1040, 2600, 5200])
def test_forecast_timing_for_long_series(api, uid, weeks):
    s = uid()
    _series(api, s, synth_series(weeks, "count", seed=7))
    t0 = time.perf_counter()
    r = api.get(f"/api/v1/series/{s}/forecast")
    dt = time.perf_counter() - t0
    assert r.status_code == 200
    REC.timings[f"forecast_{weeks}w_s"] = round(dt, 2)
    REC.timings[f"forecast_{weeks}w_policy"] = r.json()["policy"]["reason"]
    assert dt < 60, f"{weeks} weekly points took {dt:.0f}s"
    api.w("DELETE", f"/api/v1/series/{s}")
