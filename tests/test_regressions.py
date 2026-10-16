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
