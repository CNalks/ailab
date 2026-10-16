"""Gate over the REST API: forecast/policy/quality/gate endpoints, as_of, config validation."""

import pytest
from fastapi.testclient import TestClient

from ailab.api.app import create_app
from ailab.api.settings import ConfigError, Settings

from .conftest import SL, count_series
from .test_api import KEY, H, body

NEW_POLICY_FIELDS = {"gate_decision", "reason_codes", "limited"}
OLD_POLICY_FIELDS = {
    "forecast_allowed",
    "reason",
    "display_mode",
    "model_family",
    "horizon_weeks",
    "indicator_type",
    "warnings",
}


@pytest.fixture(scope="module")
def client():
    c = TestClient(create_app(settings=Settings(api_key=KEY)))
    assert (
        c.post("/api/v1/series", json=body("good", count_series(8)), headers=H).status_code == 201
    )
    assert (
        c.post("/api/v1/series", json=body("short", count_series(2)), headers=H).status_code == 201
    )
    gappy = count_series(8)
    gappy.loc[::8, "y"] = float("nan")
    assert c.post("/api/v1/series", json=body("gappy", gappy), headers=H).status_code == 201
    return c


def test_refusal_is_http_200_with_reasons_and_old_fields_are_intact(client):
    r = client.get("/api/v1/series/short/forecast?horizon=4")
    assert r.status_code == 200
    j = r.json()
    assert OLD_POLICY_FIELDS | NEW_POLICY_FIELDS <= set(j["policy"])
    assert j["policy"]["forecast_allowed"] is False and j["forecast"] is None
    assert j["policy"]["reason"] == "history_lt_3_seasons"  # legacy reason string unchanged
    g = j["gate"]
    assert g["decision"] == "refused" and g["forecast_allowed"] is False
    assert "too_short" in g["reason_codes"] and j["policy"]["reason_codes"] == g["reason_codes"]
    assert g["recommended_action"]["code"] == "collect_more_history"
    assert set(g["reasons"][0]["message"]) == {"en", "zh", "fr"}
    assert g["thresholds"]["min_seasons_block"] == 3.0 and g["metrics"]["n_periods"] == SL * 2


def test_good_series_allowed_and_gate_endpoint_matches(client):
    j = client.get("/api/v1/series/good/forecast?horizon=4").json()
    assert j["policy"]["forecast_allowed"] is True and j["forecast"]["points"]
    g = client.get("/api/v1/series/good/gate?horizon=4").json()
    assert (
        g == j["gate"] and g["stage"] == "data+backtest" and g["decision"] in ("allowed", "limited")
    )
    assert "backtest" in g["metrics"] and g["gate_version"] == "1"


def test_limited_mode_over_api(client):
    j = client.get("/api/v1/series/gappy/forecast?horizon=8").json()
    assert j["gate"]["decision"] == "limited" and j["policy"]["limited"] is True
    assert j["policy"]["forecast_allowed"] is True and j["policy"]["horizon_weeks"] <= 4
    assert j["gate"]["limits"]["interval_inflation"] > 1


def test_policy_endpoint_exposes_gate_and_old_shape(client):
    p = client.get("/api/v1/series/good/policy?horizon=4").json()
    assert (
        OLD_POLICY_FIELDS | NEW_POLICY_FIELDS <= set(p)
        and p["gate"]["decision"] == p["gate_decision"]
    )


def test_quality_endpoint_shows_gate_metrics(client):
    q = client.get("/api/v1/series/good/quality").json()
    assert {"grade", "checks", "n_weeks"} <= set(q)  # legacy shape
    gm = q["gate"]
    assert gm["stage"] == "data" and "seasonal_strength" in gm["metrics"]
    assert "stationarity" in gm["metrics"] and "outliers" in gm["metrics"]
    short = client.get("/api/v1/series/short/quality").json()
    assert short["gate"]["decision"] == "refused" and "too_short" in short["gate"]["reason_codes"]


def test_as_of_enables_stale_check_and_validates_date(client):
    j = client.get("/api/v1/series/good/gate?horizon=4&as_of=2035-01-01").json()
    assert j["decision"] == "refused" and "stale_data" in j["reason_codes"]
    assert j["as_of"] == "2035-01-01"
    j = client.get("/api/v1/series/good/forecast?horizon=4&as_of=2035-01-01").json()
    assert j["policy"]["forecast_allowed"] is False and j["policy"]["reason"] == "stale_data"
    assert client.get("/api/v1/series/good/gate?as_of=not-a-date").status_code == 422
    assert client.get("/api/v1/series/nope/gate").status_code == 404


def test_series_meta_accepts_gate_overrides_and_rejects_bad_ones(client):
    ok = body("ovr", count_series(2), gate={"min_seasons_block": 1.5})
    assert client.post("/api/v1/series", json=ok, headers=H).status_code == 201
    j = client.get("/api/v1/series/ovr/gate").json()
    assert "too_short" not in j["reason_codes"]
    assert j["threshold_sources"]["min_seasons_block"] == "series"
    bad = body("badovr", count_series(2), gate={"nonsense": 1})
    r = client.post("/api/v1/series", json=bad, headers=H)
    assert r.status_code == 422
    bad = body("badmask", count_series(2), mask_periods=[["2020-02-01", "2020-01-01"]])
    assert client.post("/api/v1/series", json=bad, headers=H).status_code == 422


def test_alerts_scan_returns_gate_and_confidence(client):
    j = client.get("/api/v1/series/good/alerts?method=farrington").json()
    assert j["status"] in ("ok", "degraded") and "gate" in j
    s = client.get("/api/v1/series/short/alerts?method=ears_c2").json()
    assert s["status"] == "refused" and s["latest_is_signal"] is False
    assert client.get("/api/v1/series/good/alerts?method=bogus").status_code == 422


def test_bad_gate_environment_fails_fast(monkeypatch):
    monkeypatch.setenv("AILAB_GATE_MAX_GAP", "banana")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.setenv("AILAB_GATE_MAX_GAP", "3")
    assert Settings.from_env().gate_stale_check is False
    monkeypatch.setenv("AILAB_GATE_STALE_CHECK", "1")
    assert Settings.from_env().gate_stale_check is True


def test_env_threshold_changes_service_behaviour(monkeypatch):
    monkeypatch.setenv("AILAB_GATE_MIN_SEASONS_BLOCK", "1")
    c = TestClient(create_app(settings=Settings(api_key=KEY)))
    c.post("/api/v1/series", json=body("s2", count_series(2)), headers=H)
    assert "too_short" not in c.get("/api/v1/series/s2/gate").json()["reason_codes"]


def test_export_contains_gate(tmp_path):
    import json

    from ailab.export import export_site
    from ailab.store import SeriesStore

    from .conftest import meta

    st = SeriesStore()
    st.upsert_series(meta("good"), count_series(8))
    st.upsert_series(meta("short"), count_series(2))
    man = export_site(st, tmp_path, horizon=4)
    by = {e["series_id"]: e for e in man["series"]}
    assert by["good"]["gate"]["decision"] in ("allowed", "limited")
    assert by["short"]["gate"]["decision"] == "refused"
    assert by["short"]["gate"]["reason_codes"] == ["too_short"]
    doc = json.loads((tmp_path / "series" / "short.json").read_text())
    assert doc["gate"]["recommended_action"]["code"] == "collect_more_history"
    assert doc["policy"]["gate_decision"] == "refused"
