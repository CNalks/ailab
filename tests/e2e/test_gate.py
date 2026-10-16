"""Quality gate over HTTP: refusals are 200 + reasons, limited mode, quality/gate endpoints."""

import math

import pytest

from .conftest import sid

SEASONAL = [
    100 + 80 * max(0.0, math.sin(2 * math.pi * i / 52 - 0.5)) ** 2 + (i * 7 % 5)
    for i in range(52 * 6)
]


def test_refused_short_series_is_200_with_machine_and_human_reasons(api, mk):
    s = mk([10.0 + (i % 5) for i in range(60)])
    r = api.get(f"/api/v1/series/{s}/forecast")
    assert r.status_code == 200
    j = r.json()
    assert j["policy"]["forecast_allowed"] is False and j["forecast"] is None
    g = j["gate"]
    assert g["decision"] == "refused" and "too_short" in g["reason_codes"]
    msg = g["reasons"][0]["message"]
    assert set(msg) == {"en", "zh", "fr"} and all(msg.values())
    assert g["metrics"]["n_periods"] == 60 and g["thresholds"]["min_seasons_block"] == 3.0
    assert g["recommended_action"]["code"] == "collect_more_history"
    assert api.get(f"/api/v1/series/{s}/gate").json() == g


def test_constant_series_refused_with_constant_code(api, mk):
    s = mk([50.0] * (52 * 4))
    g = api.get(f"/api/v1/series/{s}/gate").json()
    assert g["decision"] == "refused" and "constant_series" in g["reason_codes"]


def test_good_seasonal_series_is_allowed_and_quality_shows_gate_metrics(api, mk):
    s = mk([round(v) for v in SEASONAL])
    q = api.get(f"/api/v1/series/{s}/quality").json()
    gm = q["gate"]
    assert gm["decision"] == "allowed" and gm["metrics"]["seasonal_strength"] > 0.5
    assert {"adf_p", "kpss_p", "class"} <= set(gm["metrics"]["stationarity"])
    f = api.get(f"/api/v1/series/{s}/forecast", params={"horizon": 4}).json()
    assert f["gate"]["decision"] in ("allowed", "limited")
    assert (
        f["gate"]["stage"] == "data+backtest"
        and f["policy"]["gate_decision"] == f["gate"]["decision"]
    )


def test_demo_fluview_series_pass_the_gate(api):
    for sid_ in ("us_ilinet_wili", "us_ilinet_ili_visits"):
        g = api.get(f"/api/v1/series/{sid_}/gate", params={"horizon": 4}).json()
        assert g["decision"] == "allowed", (sid_, g["reason_codes"])
        assert not [r for r in g["reasons"] if r["severity"] in ("block", "limit")]


def test_as_of_validation_and_stale(api, mk):
    s = mk([round(v) for v in SEASONAL])
    assert api.get(f"/api/v1/series/{s}/gate", params={"as_of": "garbage"}).status_code == 422
    g = api.get(f"/api/v1/series/{s}/gate", params={"as_of": "2100-01-01"}).json()
    assert g["decision"] == "refused" and "stale_data" in g["reason_codes"]


def test_unknown_series_gate_404(api):
    assert api.get(f"/api/v1/series/{sid('nope')}/gate").status_code == 404


@pytest.mark.parametrize("bad", [{"nonsense": 1}, {"max_gap": "x"}, {"seasonal_expected": "maybe"}])
def test_bad_gate_override_rejected(api, bad):
    body = {
        "meta": {"series_id": sid("badgate"), "name": "x", "indicator_type": "count", "gate": bad},
        "observations": [{"ds": "2020-01-05", "y": 1.0}],
    }
    assert api.w("POST", "/api/v1/series", json=body).status_code == 422
