"""REST API via TestClient on small synthetic series (no demo data, fast)."""

import pytest
from fastapi.testclient import TestClient

from ailab.api.app import create_app
from ailab.api.settings import ConfigError, Settings
from ailab.synth import synth_pair

from .conftest import SL, count_series

KEY = "k" * 16 + "-test-key"
H = {"X-API-Key": KEY}


def obs(df):
    return [
        {"ds": d.date().isoformat(), "y": None if v != v else float(v)}
        for d, v in zip(df["ds"], df["y"], strict=True)
    ]


def body(sid, df, kind="count", **meta):
    return {
        "meta": {
            "series_id": sid,
            "name": sid.upper(),
            "indicator_type": kind,
            "season_length": SL,
            **meta,
        },
        "observations": obs(df),
    }


@pytest.fixture
def client():
    c = TestClient(create_app(settings=Settings(api_key=KEY)))
    num, den = synth_pair(SL * 8, seed=2, season_length=SL)
    assert c.post("/api/v1/series", json=body("pos", num), headers=H).status_code == 201
    assert c.post("/api/v1/series", json=body("tests", den), headers=H).status_code == 201
    return c


def test_health_ready_docs_openapi(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["version"] and h["db"]["ok"] and h["store"]["series"] == 2
    assert h["writable"] is True
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "ailab service API"
    paths = set(spec["paths"])
    for p in (
        "/health",
        "/ready",
        "/api/v1/series",
        "/api/v1/series/{series_id}",
        "/api/v1/series/{series_id}/data",
        "/api/v1/series/{series_id}/lineage",
        "/api/v1/series/{series_id}/forecast",
        "/api/v1/series/{series_id}/policy",
        "/api/v1/series/{series_id}/alerts",
        "/api/v1/series/{series_id}/export",
        "/api/v1/series/{series_id}/recompute",
        "/api/v1/derive",
        "/api/v1/derive/preview",
        "/api/v1/alerts/rules",
        "/api/v1/alerts/evaluate",
        "/api/v1/export",
    ):
        assert p in paths, p
    assert "422" in spec["paths"]["/api/v1/derive"]["post"]["responses"]


def test_ready_reports_503_when_db_closed():
    app = create_app(settings=Settings())
    app.state.store.close()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
    assert c.get("/health").json()["status"] == "degraded"


def test_list_get_pagination_and_filters(client):
    j = client.get("/api/v1/series", params={"limit": 1}).json()
    assert j["total"] == 2 and len(j["items"]) == 1 and j["next_offset"] == 1
    j2 = client.get("/api/v1/series", params={"limit": 1, "offset": 1}).json()
    assert j2["items"][0]["series_id"] == "tests" and j2["next_offset"] is None
    assert client.get("/api/v1/series", params={"indicator_type": "rate"}).json()["total"] == 0
    assert client.get("/api/v1/series", params={"derived": True}).json()["total"] == 0
    for bad in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"indicator_type": "x"}):
        assert client.get("/api/v1/series", params=bad).status_code == 422
    assert client.get("/api/v1/series/pos").json()["indicator_type"] == "count"
    assert client.get("/api/v1/series/nope").status_code == 404
    assert client.get("/api/v1/series/BAD-ID").status_code == 422


def test_data_endpoint_paging_and_dates(client):
    j = client.get("/api/v1/series/pos/data", params={"limit": 10, "offset": 5}).json()
    assert j["total"] == SL * 8 and len(j["items"]) == 10 and j["next_offset"] == 15
    assert j["indicator_type"] == "count"
    r = client.get("/api/v1/series/pos/data", params={"start": "2012-02-01", "end": "2012-03-01"})
    assert r.status_code == 200 and 0 < r.json()["total"] < 10
    assert client.get("/api/v1/series/pos/data", params={"start": "garbage"}).status_code == 422
    assert client.get("/api/v1/series/pos/data", params={"limit": 5001}).status_code == 422
    assert client.get("/api/v1/series/pos/observations").status_code == 200  # deprecated alias


def test_create_series_validation(client):
    df = count_series(1)
    ok = body("new1", df)
    no_kind = {"meta": {"series_id": "new2", "name": "x"}, "observations": obs(df)}
    r = client.post("/api/v1/series", json=no_kind, headers=H)
    assert r.status_code == 422 and "indicator_type" in r.json()["error"]["message"]
    for bad_meta in (
        {"derived_expr": "a+b"},
        {"derived_from": ["x"]},
        {"lineage": {"x": 1}},
        {"bindings": {"A": "x"}},
    ):
        b = body("new3", df, **bad_meta)
        assert client.post("/api/v1/series", json=b, headers=H).status_code == 422
    neg = body("neg", df)
    neg["observations"][0]["y"] = -1
    assert client.post("/api/v1/series", json=neg, headers=H).status_code == 422
    dup = body("dup", df)
    dup["observations"].append(dup["observations"][0])
    assert client.post("/api/v1/series", json=dup, headers=H).status_code == 422
    offgrid = body("off", df)
    offgrid["observations"][1]["ds"] = "2012-01-09"
    assert client.post("/api/v1/series", json=offgrid, headers=H).status_code == 422
    empty = body("empty", df)
    empty["observations"] = []
    assert client.post("/api/v1/series", json=empty, headers=H).status_code == 422
    badprop = body("bp", df, "proportion")
    assert client.post("/api/v1/series", json=badprop, headers=H).status_code == 422  # > 1
    notnum = body("nn", df)
    notnum["observations"][0]["y"] = "abc"
    assert client.post("/api/v1/series", json=notnum, headers=H).status_code == 422
    assert client.post("/api/v1/series", json=ok, headers=H).status_code == 201
    r = client.post("/api/v1/series", json=ok, headers=H)  # exists
    assert r.status_code == 409
    r = client.post("/api/v1/series", params={"overwrite": True}, json=ok, headers=H)
    assert r.status_code == 201 and r.json()["replaced"] is True
    # JSON null = missing, kept as null
    m = body("withnull", df)
    m["observations"][2]["y"] = None
    assert client.post("/api/v1/series", json=m, headers=H).status_code == 201
    got = client.get("/api/v1/series/withnull/data").json()["items"]
    assert got[2]["y"] is None


def test_observation_and_series_count_limits():
    s = Settings(api_key=KEY, max_observations=20, max_series=1)
    c = TestClient(create_app(settings=s))
    assert c.post("/api/v1/series", json=body("a", count_series(2)), headers=H).status_code == 422
    small = count_series(1).iloc[:15]
    assert c.post("/api/v1/series", json=body("a", small), headers=H).status_code == 201
    r = c.post("/api/v1/series", json=body("b", small), headers=H)
    assert r.status_code == 409 and "limit" in r.json()["error"]["message"]


def test_derive_create_lineage_and_get_data(client):
    r = client.post(
        "/api/v1/derive",
        headers=H,
        json={"series_id": "pos_rate", "name": "Positivity", "expr": "pos / tests"},
    )
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["series"]["indicator_type"] == "proportion" and j["stored"] is True
    assert j["inferred_indicator_type"] == "proportion" and j["sample"]
    lin = client.get("/api/v1/series/pos_rate/lineage").json()
    assert lin["is_derived"] and lin["derived_from"] == ["pos", "tests"]
    assert lin["lineage"]["alignment"]["mode"] == "intersection"
    assert client.get("/api/v1/series/pos/lineage").json()["dependents"] == ["pos_rate"]
    d = client.get("/api/v1/series/pos_rate/data", params={"limit": 3}).json()
    assert d["indicator_type"] == "proportion" and all(0 <= o["y"] <= 1 for o in d["items"])
    assert client.get("/api/v1/series", params={"derived": True}).json()["total"] == 1
    # forecast works on derived proportions (policy decides)
    f = client.get("/api/v1/series/pos_rate/policy")
    assert f.status_code == 200 and f.json()["indicator_type"] == "proportion"
    # recompute
    rc = client.post("/api/v1/series/pos_rate/recompute", headers=H)
    assert rc.status_code == 200 and "recomputed_at" in rc.json()["series"]["lineage"]
    assert client.post("/api/v1/series/pos/recompute", headers=H).status_code == 409


def test_derive_preview_stores_nothing_and_needs_no_key(client):
    j = client.post(
        "/api/v1/derive/preview",
        json={
            "series_id": "p1",
            "name": "P",
            "expr": "100 * A / B",
            "bindings": {"A": "pos", "B": "tests"},
        },
    ).json()
    assert j["stored"] is False and j["series"]["value_scale"] == 100
    assert client.get("/api/v1/series/p1").status_code == 404


def test_derive_errors_are_structured(client):
    def post(**kw):
        b = {"series_id": "x1", "name": "x", "expr": "pos + tests", **kw}
        return client.post("/api/v1/derive", headers=H, json=b)

    r = post(expr="__import__('os').system('id')")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_expression"
    assert post(expr="pos + nothere").json()["error"]["code"] == "invalid_expression"
    assert post(expr="rolling_mean(pos / tests, 3)").status_code == 422
    r = post(expr="pos / tests", indicator_type="count")
    assert r.status_code == 422 and "infers" in r.json()["error"]["message"]
    assert post(extra_field=1).status_code == 422  # unknown fields forbidden
    assert post(series_id="BAD ID").status_code == 422
    assert post(expr="x" * 301).status_code == 422
    assert post(align="outer").status_code == 422
    assert client.get("/api/v1/series/x1").status_code == 404  # nothing stored by failures
    assert post().status_code == 201
    assert post().status_code == 409  # exists
    assert post(overwrite=True).status_code == 201
    assert post(series_id="pos").status_code == 409  # cannot overwrite a base series
    assert post(series_id="x2", expr="x1 + x1").status_code == 201
    r = post(series_id="x1", expr="x2 + x2", overwrite=True)
    assert r.status_code == 409 and "cycle" in r.json()["error"]["message"]


def test_delete_with_dependents_and_cascade(client):
    client.post(
        "/api/v1/derive", headers=H, json={"series_id": "dd", "name": "d", "expr": "pos + tests"}
    )
    r = client.delete("/api/v1/series/pos", headers=H)
    assert r.status_code == 409 and r.json()["error"]["details"]["dependents"] == ["dd"]
    assert client.delete("/api/v1/series/dd", headers=H).json() == {"deleted": ["dd"]}
    assert client.delete("/api/v1/series/pos", headers=H).json() == {"deleted": ["pos"]}
    assert client.delete("/api/v1/series/pos", headers=H).status_code == 404
    client.post(
        "/api/v1/derive", headers=H, json={"series_id": "dd2", "name": "d", "expr": "tests * 2"}
    )
    r = client.delete("/api/v1/series/tests", params={"cascade": True}, headers=H)
    assert r.json()["deleted"] == ["dd2", "tests"]


def test_forecast_policy_and_alerts_endpoints(client):
    r = client.get("/api/v1/series/pos/forecast", params={"horizon": 4})
    assert r.status_code == 200
    j = r.json()
    assert j["policy"]["indicator_type"] == "count" and "backtest" in j and "quality" in j
    assert j["backtest"]["seasons_covered"] > 0
    if j["policy"]["forecast_allowed"]:
        assert len(j["forecast"]["points"]) == j["policy"]["horizon_weeks"]
    assert client.get("/api/v1/series/pos/forecast", params={"horizon": 27}).status_code == 422
    assert client.get("/api/v1/series/nope/forecast").status_code == 404
    a = client.get("/api/v1/series/pos/alerts", params={"method": "cusum"}).json()
    assert a["method"] == "cusum" and "latest" in a
    assert client.get("/api/v1/series/pos/alerts", params={"method": "bad"}).status_code == 422
    assert client.get("/api/v1/series/pos/quality").json()["grade"] in "ABC"
    rule = {"rule_id": "r_pos", "series_id": "pos", "method": "ears_c3"}
    assert client.put("/api/v1/alerts/rules", headers=H, json=rule).status_code == 200
    ev = client.get("/api/v1/alerts/evaluate").json()
    assert ev["total"] == 1 and ev["items"][0]["rule"]["rule_id"] == "r_pos"
    assert client.get("/api/v1/alerts/rules", params={"series_id": "pos"}).json()[0]["rule_id"]
    assert (
        client.put(
            "/api/v1/alerts/rules", headers=H, json={**rule, "series_id": "nope"}
        ).status_code
        == 404
    )
    assert client.delete("/api/v1/alerts/rules/r_pos", headers=H).status_code == 200
    assert client.delete("/api/v1/alerts/rules/r_pos", headers=H).status_code == 404


def test_series_export_formats(client):
    csv = client.get("/api/v1/series/pos/export", params={"format": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0] == "series_id,period_start,value"
    assert "attachment" in csv.headers["content-disposition"]
    js = client.get("/api/v1/series/pos/export", params={"format": "json"}).json()
    assert js["series"]["series_id"] == "pos" and len(js["observations"]) == SL * 8
    pq = client.get("/api/v1/series/pos/export", params={"format": "parquet"})
    assert pq.status_code == 200 and pq.content[:4] == b"PAR1"
    assert client.get("/api/v1/series/pos/export", params={"format": "xml"}).status_code == 422


def test_static_export_endpoint(tmp_path):
    c = TestClient(create_app(settings=Settings(api_key=KEY, out_dir=tmp_path / "o")))
    c.post("/api/v1/series", json=body("a", count_series(8, seed=1)), headers=H)
    c.post("/api/v1/series", json=body("b", count_series(8, seed=2)), headers=H)
    c.post("/api/v1/derive", headers=H, json={"series_id": "ab", "name": "ab", "expr": "a + b"})
    r = c.post("/api/v1/export", headers=H)
    assert r.status_code == 200
    assert {s["series_id"] for s in r.json()["series"]} == {"a", "b", "ab"}
    entry = next(s for s in r.json()["series"] if s["series_id"] == "ab")
    assert entry["derived_from"] == ["a", "b"]
    assert (tmp_path / "o" / "manifest.json").exists()


def test_settings_validation(monkeypatch):
    with pytest.raises(ConfigError):
        Settings(api_key="short")
    monkeypatch.setenv("AILAB_MAX_SERIES", "abc")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("AILAB_MAX_SERIES", "0")
    with pytest.raises(ConfigError):
        Settings.from_env()
