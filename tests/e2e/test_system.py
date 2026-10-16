"""System endpoints: /health /ready /docs /redoc /openapi.json, error shape, headers."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_health(api):
    r = api.get("/health")
    assert r.status_code == 200
    h = r.json()
    assert h["status"] == "ok" and h["db"]["ok"] is True and h["writable"] is True
    assert re.match(r"^\d+\.\d+\.\d+", h["version"]) and h["uptime_s"] >= 0
    assert "server" not in {k.lower() for k in r.headers}


def test_health_readonly_flag(ro):
    h = ro.get("/health").json()
    assert h["writable"] is False and h["status"] == "ok"


def test_ready(api):
    r = api.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready" and r.json()["checks"] == {"db": True, "schema": True}


def test_docs_and_redoc(api):
    r = api.get("/docs")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "swagger" in r.text.lower()
    assert api.get("/redoc").status_code in (200, 404)  # served by FastAPI by default


def test_openapi_json_valid_and_complete(api):
    r = api.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.")
    ops = {(m.upper(), p) for p, v in spec["paths"].items() for m in v if m != "parameters"}
    expected = {
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/api/v1/series"),
        ("POST", "/api/v1/series"),
        ("GET", "/api/v1/series/{series_id}"),
        ("DELETE", "/api/v1/series/{series_id}"),
        ("GET", "/api/v1/series/{series_id}/data"),
        ("GET", "/api/v1/series/{series_id}/observations"),
        ("GET", "/api/v1/series/{series_id}/quality"),
        ("GET", "/api/v1/series/{series_id}/lineage"),
        ("GET", "/api/v1/series/{series_id}/export"),
        ("GET", "/api/v1/series/{series_id}/forecast"),
        ("GET", "/api/v1/series/{series_id}/policy"),
        ("GET", "/api/v1/series/{series_id}/alerts"),
        ("POST", "/api/v1/series/{series_id}/recompute"),
        ("POST", "/api/v1/derive"),
        ("POST", "/api/v1/derive/preview"),
        ("GET", "/api/v1/alerts/rules"),
        ("PUT", "/api/v1/alerts/rules"),
        ("DELETE", "/api/v1/alerts/rules/{rule_id}"),
        ("GET", "/api/v1/alerts/evaluate"),
        ("POST", "/api/v1/export"),
    }
    assert expected <= ops, expected - ops
    assert ops - expected == set(), f"undocumented in tests: {ops - expected}"


def test_committed_openapi_is_current(api):
    """docs/openapi.json must match the running service (regenerate: `make openapi`)."""
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text())
    live = api.get("/openapi.json").json()
    assert committed == live, "docs/openapi.json is stale: run `make openapi`"


def test_error_shape_404_405(api):
    r = api.get("/nope")
    assert r.status_code == 404
    e = r.json()["error"]
    assert e["code"] == "not_found" and e["request_id"] == r.headers["x-request-id"]
    r = api.post("/health")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"
    r = api.get("/api/v1/series/nope_nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_request_id_roundtrip_and_sanitising(api):
    r = api.get("/health", headers={"X-Request-ID": "abc-123_XYZ"})
    assert r.headers["x-request-id"] == "abc-123_XYZ"
    r = api.get("/health", headers={"X-Request-ID": "bad id with spaces <script>"})
    assert r.headers["x-request-id"] != "bad id with spaces <script>"
    assert re.match(r"^[A-Za-z0-9_-]{1,64}$", r.headers["x-request-id"])
    r = api.get("/health", headers={"X-Request-ID": "x" * 200})
    assert len(r.headers["x-request-id"]) <= 64


def test_no_traceback_or_server_banner(api):
    r = api.get("/api/v1/series/%00")
    assert r.status_code in (404, 422)
    assert "Traceback" not in r.text and "server" not in {k.lower() for k in r.headers}
