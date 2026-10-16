"""Regression tests for bugs found by the e2e suite (see docs/TEST_REPORT.md)."""

from fastapi.testclient import TestClient

from ailab import export, pipeline
from ailab.api.app import create_app
from ailab.api.settings import Settings
from ailab.synth import synth_series

from .conftest import meta

KEY = "k" * 16 + "-regress"


def test_model_history_is_capped_for_very_long_series(monkeypatch):
    """A 5200-week series used to take > 4 minutes to forecast (quadratic backtest cost)."""
    monkeypatch.setattr(pipeline, "MAX_MODEL_WEEKS", 130)
    df = synth_series(300, "count", seed=3, season_length=13)
    res = pipeline.analyse_frame(meta(), df, include_observations=False)
    assert res["model_input"]["model_weeks_used"] == 130
    assert res["model_input"]["model_weeks_cap"] == 130
    assert res["quality"]["n_weeks"] == 300  # quality still looks at everything
    if res["policy"]["forecast_allowed"]:
        assert res["forecast"]["origin"] == str(df["ds"].iloc[-1].date())
    assert any("most recent 130 weeks" in w for w in res["policy"]["warnings"])


def test_short_series_is_not_marked_truncated():
    df = synth_series(13 * 8, "count", seed=3, season_length=13)
    res = pipeline.analyse_frame(meta(), df, include_observations=False)
    assert res["model_input"]["model_weeks_used"] == 13 * 8
    assert not any("most recent" in w for w in res["policy"]["warnings"])


def test_export_budget_error_is_a_422_not_a_500(monkeypatch, tmp_path):
    monkeypatch.setattr(export, "MAX_SERIES_GZ", 10)
    c = TestClient(create_app(settings=Settings(api_key=KEY, out_dir=tmp_path, preload_demo=True)))
    r = c.post("/api/v1/export", headers={"X-API-Key": KEY})
    assert r.status_code == 422 and r.json()["error"]["code"] == "export_budget_exceeded"


def test_parallel_create_same_id_is_atomic():
    """check-then-write was not atomic: several parallel POSTs could all answer 201 and the last
    one silently replaced the others although overwrite=false."""
    import collections
    import threading

    c = TestClient(create_app(settings=Settings(api_key=KEY)))
    n = 16
    barrier = threading.Barrier(n)
    out = []

    def post(i):
        import datetime as dt

        d0 = dt.date(2020, 1, 5)
        body = {
            "meta": {"series_id": "race", "name": "n", "indicator_type": "count"},
            "observations": [
                {"ds": (d0 + dt.timedelta(weeks=j)).isoformat(), "y": float(i)} for j in range(300)
            ],
        }
        barrier.wait()
        out.append(c.post("/api/v1/series", json=body, headers={"X-API-Key": KEY}).status_code)

    ts = [threading.Thread(target=post, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert collections.Counter(out) == {201: 1, 409: n - 1}, out


def test_series_deleted_between_check_and_read_is_404_not_500(monkeypatch):
    """Concurrent DELETE + GET used to surface a KeyError as HTTP 500."""
    c = TestClient(create_app(settings=Settings(api_key=KEY)), raise_server_exceptions=False)
    body = {
        "meta": {"series_id": "gone", "name": "n", "indicator_type": "count"},
        "observations": [{"ds": "2020-01-05", "y": 1.0}, {"ds": "2020-01-12", "y": 2.0}],
    }
    assert c.post("/api/v1/series", json=body, headers={"X-API-Key": KEY}).status_code == 201
    store = c.app.state.store
    real = store.get_observations

    def vanish(*a, **k):
        store.delete_series("gone")
        return real(*a, **k)

    monkeypatch.setattr(store, "get_observations", vanish)
    r = c.get("/api/v1/series/gone/data")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
