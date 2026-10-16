"""Auth defaults, size limits, CORS, structured errors, log hygiene."""

import logging

import pytest
from fastapi.testclient import TestClient

from ailab.api.app import create_app
from ailab.api.settings import Settings

from .conftest import count_series
from .test_api import KEY, H, body

WRITE_CALLS = [
    ("post", "/api/v1/series", lambda: body("w1", count_series(1))),
    ("delete", "/api/v1/series/anything", None),
    ("post", "/api/v1/derive", lambda: {"series_id": "d", "name": "d", "expr": "a + 1"}),
    ("post", "/api/v1/series/anything/recompute", None),
    ("put", "/api/v1/alerts/rules", lambda: {"rule_id": "r", "series_id": "anything"}),
    ("delete", "/api/v1/alerts/rules/r", None),
    ("post", "/api/v1/export", None),
]


def _call(c, method, path, payload, headers=None):
    kw = {"headers": headers or {}}
    if payload is not None:
        kw["json"] = payload()
    return getattr(c, method)(path, **kw)


@pytest.mark.parametrize("method,path,payload", WRITE_CALLS)
def test_no_key_configured_means_every_write_is_forbidden(method, path, payload):
    c = TestClient(create_app(settings=Settings()))  # no AILAB_API_KEY
    assert _call(c, method, path, payload).status_code == 403
    assert _call(c, method, path, payload, {"X-API-Key": KEY}).status_code == 403
    assert c.get("/health").json()["writable"] is False


@pytest.mark.parametrize("method,path,payload", WRITE_CALLS)
def test_writes_need_the_exact_key(method, path, payload):
    c = TestClient(create_app(settings=Settings(api_key=KEY)))
    assert _call(c, method, path, payload).status_code == 401
    assert _call(c, method, path, payload, {"X-API-Key": "x" * 25}).status_code == 401
    assert _call(c, method, path, payload, {"X-API-Key": KEY + "x"}).status_code == 401
    r = _call(c, method, path, payload, {"X-API-Key": KEY[:-1]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    # the right key passes authentication (outcome may be 4xx for other reasons, never 401/403)
    assert _call(c, method, path, payload, H).status_code not in (401, 403)


def test_reads_are_open_and_preview_is_read_only():
    c = TestClient(create_app(settings=Settings()))
    for path in (
        "/health",
        "/ready",
        "/docs",
        "/openapi.json",
        "/api/v1/series",
        "/api/v1/alerts/rules",
        "/api/v1/alerts/evaluate",
    ):
        assert c.get(path).status_code == 200, path


def test_weak_key_refused_at_startup():
    with pytest.raises(ValueError):
        create_app(api_key="123", db_path=":memory:")


def test_structured_errors_everywhere():
    c = TestClient(create_app(settings=Settings(api_key=KEY)), raise_server_exceptions=False)
    cases = [
        c.get("/api/v1/series/nope"),  # 404 from handler
        c.get("/no/such/route"),  # 404 from router
        c.get("/api/v1/series", params={"limit": "x"}),  # 422 validation
        c.post("/api/v1/series", json={"nonsense": 1}, headers=H),  # 422 body
        c.post(
            "/api/v1/series",
            content=b"{not json",
            headers={**H, "Content-Type": "application/json"},
        ),
        c.put("/health"),  # 405
        c.post("/api/v1/series", json={}),  # 401
    ]
    seen = set()
    for r in cases:
        j = r.json()
        assert set(j) == {"error"} and {"code", "message", "request_id"} <= set(j["error"]), r.text
        assert r.headers["x-request-id"] == j["error"]["request_id"]
        seen.add(r.status_code)
    assert {404, 405, 422, 401} <= seen


def test_validation_errors_do_not_echo_input():
    c = TestClient(create_app(settings=Settings(api_key=KEY)))
    secret = "SUPER-SECRET-PAYLOAD-VALUE"
    r = c.post(
        "/api/v1/series", json={"meta": {"series_id": secret}, "observations": [secret]}, headers=H
    )
    assert r.status_code == 422 and secret not in r.text


def test_unhandled_exception_returns_500_without_traceback(caplog):
    app = create_app(settings=Settings())

    @app.get("/boom")
    def boom():
        raise RuntimeError("database password is hunter2")

    c = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="ailab.api"):
        r = c.get("/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "hunter2" not in r.text and "Traceback" not in r.text
    assert r.json()["error"]["request_id"] in caplog.text  # correlatable in the server log


def test_request_size_limit_content_length_and_streaming():
    c = TestClient(create_app(settings=Settings(api_key=KEY, max_body_bytes=2048)))
    big = body("big", count_series(10))
    r = c.post("/api/v1/series", json=big, headers=H)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    # chunked upload without a Content-Length is cut off as well
    def gen():
        for _ in range(100):
            yield b"x" * 100

    r = c.post("/api/v1/series", content=gen(), headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 413
    ok = body("ok", count_series(1).iloc[:5])
    small = TestClient(create_app(settings=Settings(api_key=KEY, max_body_bytes=64 * 1024)))
    assert small.post("/api/v1/series", json=ok, headers=H).status_code == 201


def test_cors_is_off_by_default_and_configurable():
    plain = TestClient(create_app(settings=Settings()))
    r = plain.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    cfg = Settings(cors_origins=("https://site.example",))
    c = TestClient(create_app(settings=cfg))
    ok = c.get("/health", headers={"Origin": "https://site.example"})
    assert ok.headers["access-control-allow-origin"] == "https://site.example"
    assert "access-control-allow-credentials" not in ok.headers
    no = c.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in no.headers
    pre = c.options(
        "/api/v1/series",
        headers={
            "Origin": "https://site.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key,content-type",
        },
    )
    assert (
        pre.status_code == 200
        and "x-api-key" in pre.headers["access-control-allow-headers"].lower()
    )


def test_cors_origins_from_env(monkeypatch):
    monkeypatch.setenv("AILAB_CORS_ORIGINS", "https://a.example, https://b.example")
    assert Settings.from_env().cors_origins == ("https://a.example", "https://b.example")


def test_logs_never_contain_the_api_key_or_payloads(caplog):
    c = TestClient(create_app(settings=Settings(api_key=KEY)))
    with caplog.at_level(logging.DEBUG):
        c.get("/health", headers={"X-API-Key": KEY})
        c.post(
            "/api/v1/series",
            json=body("lg", count_series(1)),
            headers={"X-API-Key": "wrong-key-value-123"},
        )
        c.post("/api/v1/series", json=body("lg", count_series(1).iloc[:5]), headers=H)
        c.get("/api/v1/series", params={"q": "token=abc"})
    text = caplog.text
    assert "request_id=" in text and "POST /api/v1/series" in text
    assert KEY not in text and "wrong-key-value-123" not in text
    assert "token=abc" not in text  # query strings are not logged
    assert "observations" not in text  # nor bodies


def test_redact_filter_masks_secrets_in_messages():
    from ailab.api.middleware import RedactFilter

    f = RedactFilter(["s3cr3t-value"])
    rec = logging.LogRecord(
        "x", logging.INFO, "", 0, "boom s3cr3t-value X-API-Key: abc123", (), None
    )
    f.filter(rec)
    assert "s3cr3t-value" not in rec.msg and "abc123" not in rec.msg


def test_server_has_no_pickle_model_storage():
    import ailab.store

    src = open(ailab.store.__file__).read()
    assert "pickle" not in src
    c = TestClient(create_app(settings=Settings()))
    spec = c.get("/openapi.json").text
    assert "pickle" not in spec.lower()
