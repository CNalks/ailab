"""The synthetic test-data set (ailab.testset) driven through the REST API."""

import math
from urllib.parse import quote

import pytest

from ailab.testset import build_cases

from .conftest import sid

CASES = build_cases()
OK = [c for c in CASES if c.expect == "accept"]
BAD = [c for c in CASES if c.expect == "reject"]
SLOW = {"count_max"}


def _finite(o):
    if isinstance(o, float):
        return math.isfinite(o)
    if isinstance(o, dict):
        return all(_finite(v) for v in o.values())
    if isinstance(o, list):
        return all(_finite(v) for v in o)
    return True


@pytest.mark.parametrize("case", BAD, ids=[c.case_id for c in BAD])
def test_invalid_cases_are_rejected_with_structured_422(api, case, loaded):
    r = api.w(
        "POST", "/api/v1/series", content=case.raw(), headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 422, (r.status_code, r.text[:300])
    e = r.json()["error"]
    assert e["code"] == "validation_error" and e["request_id"]
    if case.reject_contains:
        assert case.reject_contains in (e["message"] + str(e.get("details", "")))
    if case.series_id and "\x00" not in case.series_id:
        assert api.get(f"/api/v1/series/{quote(case.series_id, safe='')}").status_code in (404, 422)


@pytest.mark.parametrize("case", OK, ids=[c.case_id for c in OK])
def test_valid_cases_are_stored_and_all_read_endpoints_work(api, case, loaded):
    s = sid(case.case_id)
    m = api.get(f"/api/v1/series/{s}")
    assert (
        m.status_code == 200 and m.json()["indicator_type"] == case.body["meta"]["indicator_type"]
    )
    n = len(case.body["observations"])
    d = api.get(f"/api/v1/series/{s}/data", params={"limit": 5000}).json()
    assert d["total"] == n and len(d["items"]) == min(n, 5000)
    if n > 5000:  # follow next_offset
        d["items"] += api.get(
            f"/api/v1/series/{s}/data", params={"limit": 5000, "offset": d["next_offset"]}
        ).json()["items"]
        assert len(d["items"]) == n
    sent = [o["y"] for o in sorted(case.body["observations"], key=lambda o: o["ds"])]
    assert [o["y"] for o in d["items"]] == [None if v is None else pytest.approx(v) for v in sent]
    q = api.get(f"/api/v1/series/{s}/quality")
    assert q.status_code == 200 and q.json()["grade"] in "ABCD"
    assert _finite(q.json())
    assert api.get(f"/api/v1/series/{s}/lineage").status_code == 200
    for method in ("ears_c1", "ears_c2", "ears_c3", "cusum"):
        a = api.get(f"/api/v1/series/{s}/alerts", params={"method": method})
        assert a.status_code == 200, (method, a.text[:200])
        assert _finite(a.json())
    for fmt in ("csv", "json", "parquet"):
        assert api.get(f"/api/v1/series/{s}/export", params={"format": fmt}).status_code == 200


@pytest.mark.parametrize(
    "case",
    [c for c in OK if c.case_id not in SLOW],
    ids=[c.case_id for c in OK if c.case_id not in SLOW],
)
def test_policy_and_forecast_invariants(api, case, loaded):
    s = sid(case.case_id)
    r = api.get(f"/api/v1/series/{s}/forecast")
    assert r.status_code == 200, r.text[:300]
    f = r.json()
    p = f["policy"]
    assert {
        "forecast_allowed",
        "reason",
        "display_mode",
        "model_family",
        "horizon_weeks",
        "indicator_type",
    } <= set(p)
    assert p["indicator_type"] == case.body["meta"]["indicator_type"] and p["reason"]
    for k, v in case.expect_policy.items():
        assert p[k] == v, (k, p)
    assert _finite(f)
    pol = api.get(f"/api/v1/series/{s}/policy").json()
    assert pol == p
    if not p["forecast_allowed"]:
        assert f["forecast"] is None and p["model_family"] == "none" and p["horizon_weeks"] == 0
        assert p["display_mode"] in ("monitor_only", "history_only")
        return
    pts = f["forecast"]["points"]
    assert len(pts) == p["horizon_weeks"] >= 1
    scale = case.body["meta"].get("value_scale", 1)
    kind = p["indicator_type"]
    for pt in pts:
        assert pt["point"] >= 0
        if kind == "proportion":
            assert pt["point"] <= scale
        if "lo95" in pt:
            assert pt["lo95"] <= pt["lo80"] <= pt["point"] <= pt["hi80"] <= pt["hi95"] + 1e-9
            assert pt["lo95"] >= 0
            if kind == "proportion":
                assert pt["hi95"] <= scale + 1e-9
    if f["backtest"]["seasons_covered"] < 3:  # counts/rates: allowed but must be flagged
        assert any("backtest spans only" in w for w in p["warnings"])
    assert f["backtest"]["baseline"] == "SeasonalNaive"


def test_derive_pair_from_testset(api, loaded):
    d = api.get(f"/api/v1/series/{sid('pair_positivity')}").json()
    assert d["indicator_type"] == "proportion" and set(d["derived_from"]) == {
        sid("pair_positives"),
        sid("pair_tests"),
    }


def test_outbreak_series_raises_signals_and_flat_series_does_not(api, loaded):
    out = api.get(f"/api/v1/series/{sid('count_outbreaks')}/alerts", params={"method": "ears_c2"})
    assert out.json()["n_signals"] >= 3
    for case_id in ("count_constant", "count_allzero"):
        flat = api.get(f"/api/v1/series/{sid(case_id)}/alerts", params={"method": "cusum"}).json()
        assert flat["n_signals"] == 0 and flat["latest_is_signal"] is False
