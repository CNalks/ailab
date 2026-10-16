"""The ONE end-to-end test on real data: bundled CDC FluView national fixture
(ailab/data/fluview_nat_fixture.csv, 835 weeks) through demo -> API -> forecast -> export."""

import gzip
import json

import pytest
from fastapi.testclient import TestClient

from ailab.api.app import create_app
from ailab.api.settings import Settings
from ailab.demo import load_fluview_demo
from ailab.export import export_site
from ailab.store import SeriesStore

KEY = "e2e-" + "k" * 20


@pytest.fixture(scope="module")
def store():
    s = SeriesStore()
    load_fluview_demo(s)
    return s


def test_fixture_kinds(store):
    wili = store.get_meta("us_ilinet_wili")
    visits = store.get_meta("us_ilinet_ili_visits")
    assert (wili.indicator_type, wili.value_scale) == ("proportion", 100)
    assert visits.indicator_type == "count"


def test_real_data_export_forecast_and_api(store, tmp_path):
    m = export_site(
        store,
        tmp_path,
        series_ids=["us_ilinet_wili"],
        horizon=4,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    entry = m["series"][0]
    blob = (tmp_path / entry["path"]).read_bytes()
    assert len(gzip.compress(blob)) < 60 * 1024
    doc = json.loads(blob)
    assert doc["backtest"]["seasons_covered"] >= 3 and doc["backtest"]["transform"] == "logit"
    assert doc["policy"]["indicator_type"] == "proportion"
    assert doc["policy"]["forecast_allowed"] is True and doc["policy"]["horizon_weeks"] >= 2
    for p in doc["forecast"]["points"]:
        assert 0 <= p["lo95"] <= p["point"] <= p["hi95"] <= 100
    assert doc["series"]["attribution"] and doc["series"]["notice"]
    # deterministic bytes
    export_site(
        store,
        tmp_path / "b",
        series_ids=["us_ilinet_wili"],
        horizon=4,
        generated_at="2026-01-01T00:00:00+00:00",
    )
    assert (tmp_path / "b" / entry["path"]).read_bytes() == blob

    c = TestClient(create_app(settings=Settings(api_key=KEY, preload_demo=True)))
    assert c.get("/api/v1/series").json()["total"] == 2
    pol = c.get("/api/v1/series/us_ilinet_wili/policy", params={"horizon": 4}).json()
    assert pol["forecast_allowed"] is True
    hdr = {"X-API-Key": KEY}
    r = c.post(
        "/api/v1/derive",
        headers=hdr,
        json={
            "series_id": "visits_ma4",
            "name": "ILI visits 4-wk mean",
            "expr": "rolling_mean(us_ilinet_ili_visits, 4)",
        },
    )
    assert r.status_code == 201 and r.json()["series"]["indicator_type"] == "count"
    assert r.json()["series"]["attribution"].startswith("Source: US CDC FluView")
