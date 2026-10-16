import gzip
import json

import pytest
from fastapi.testclient import TestClient

from ailab.api.app import create_app
from ailab.demo import load_fluview_demo
from ailab.export import BudgetError, export_site
from ailab.store import SeriesStore


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(preload_demo=True, api_key="test-key"))


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["store"]["series"] == 2 and j["writable"] is True


def test_openapi_and_docs(client):
    assert client.get("/openapi.json").json()["info"]["title"] == "ailab service API"
    assert client.get("/docs").status_code == 200


def test_series_and_forecast_with_policy(client):
    assert {s["series_id"] for s in client.get("/api/v1/series").json()} == {
        "us_ilinet_wili",
        "us_ilinet_ili_visits",
    }
    r = client.get("/api/v1/series/us_ilinet_wili/forecast", params={"horizon": 4})
    assert r.status_code == 200
    j = r.json()
    assert j["policy"]["forecast_allowed"] is True and len(j["forecast"]["points"]) == 4
    assert (
        client.get("/api/v1/series/us_ilinet_wili/policy").json()["indicator_type"] == "proportion"
    )
    assert client.get("/api/v1/series/nope/forecast").status_code == 404


def test_alerts_adhoc(client):
    j = client.get("/api/v1/series/us_ilinet_wili/alerts", params={"method": "cusum"}).json()
    assert j["method"] == "cusum" and "latest" in j
    assert (
        client.get("/api/v1/series/us_ilinet_wili/alerts", params={"method": "bad"}).status_code
        == 422
    )


def test_writes_need_key_and_readonly_without_one(client):
    body = {
        "meta": {"series_id": "x1", "name": "x"},
        "observations": [{"ds": "2024-01-07", "y": 1}],
    }
    assert client.post("/api/v1/series", json=body).status_code == 401
    assert (
        client.post("/api/v1/series", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    )
    ro = TestClient(create_app(api_key=None))
    assert (
        ro.post("/api/v1/series", json=body, headers={"X-API-Key": "anything"}).status_code == 403
    )
    assert ro.get("/health").json()["writable"] is False


def test_create_derive_rule_delete(client):
    h = {"X-API-Key": "test-key"}
    d = client.post(
        "/api/v1/derive",
        headers=h,
        json={
            "series_id": "wili_ma4",
            "name": "wILI 4-wk mean",
            "expr": "ma(us_ilinet_wili, 4)",
            "indicator_type": "proportion",
        },
    )
    assert d.status_code == 201 and d.json()["derived_from"] == ["us_ilinet_wili"]
    bad = client.post(
        "/api/v1/derive",
        headers=h,
        json={"series_id": "evil", "name": "x", "expr": "__import__('os')"},
    )
    assert bad.status_code == 422
    rule = {"rule_id": "r_wili", "series_id": "wili_ma4", "method": "ears_c3"}
    assert client.put("/api/v1/alerts/rules", headers=h, json=rule).status_code == 200
    ev = client.get("/api/v1/alerts/evaluate").json()
    assert ev and ev[0]["rule"]["rule_id"] == "r_wili"
    assert client.delete("/api/v1/alerts/rules/r_wili", headers=h).status_code == 200
    assert client.delete("/api/v1/series/wili_ma4", headers=h).status_code == 200


def test_export_format_and_budget(tmp_path):
    st = SeriesStore()
    load_fluview_demo(st)
    m = export_site(
        st,
        tmp_path,
        series_ids=["us_ilinet_wili"],
        horizon=4,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    assert (tmp_path / "manifest.json").exists() and (tmp_path / "sources.json").exists()
    entry = m["series"][0]
    blob = (tmp_path / entry["path"]).read_bytes()
    assert len(gzip.compress(blob)) < 60 * 1024
    doc = json.loads(blob)
    for key in (
        "schema_version",
        "series",
        "quality",
        "policy",
        "backtest",
        "forecast",
        "alerts",
        "observations",
        "data_through",
    ):
        assert key in doc
    assert doc["series"]["attribution"] and doc["series"]["notice"]
    assert (tmp_path / entry["download"]).exists()
    # determinism: same inputs -> byte-identical series JSON
    export_site(
        st,
        tmp_path / "b",
        series_ids=["us_ilinet_wili"],
        horizon=4,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    assert (tmp_path / "b" / entry["path"]).read_bytes() == blob
    import ailab.export as ex

    old = ex.MAX_SERIES_GZ
    ex.MAX_SERIES_GZ = 100
    try:
        with pytest.raises(BudgetError):
            export_site(st, tmp_path / "c", series_ids=["us_ilinet_wili"])
    finally:
        ex.MAX_SERIES_GZ = old
