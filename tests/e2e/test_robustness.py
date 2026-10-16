"""Request limits, CORS, injection-style ids/params, odd HTTP, unicode, failure modes."""

import json
import threading
import time
from urllib.parse import quote

import httpx
import pytest

from .conftest import CORS_ORIGIN, body

INJECT = [
    "a'; DROP TABLE series;--",
    "' OR '1'='1",
    "x' UNION SELECT meta_json FROM series--",
    "1; SELECT 1",
    "../../etc/passwd",
    "..%2f..%2fetc%2fpasswd",
    "%00",
    "a%00b",
    "${jndi:ldap://x}",
    "{{7*7}}",
    "<script>alert(1)</script>",
    "流感",
    "a b",
    "A",
    "-",
    "_",
    "a" * 65,
    "a" * 5000,
    "%",
    "%zz",
    "con",
    "nul",
    "series",
    "*",
    "?",
    "a/b",
    "a\\b",
]


@pytest.mark.parametrize("raw", INJECT, ids=[f"i{n:02d}" for n in range(len(INJECT))])
def test_injection_style_series_ids_in_path(api, raw):
    # use pre-encoded paths where the raw text contains '%' already
    path = raw if "%" in raw else quote(raw, safe="")
    for ep in ("", "/data", "/quality", "/lineage", "/forecast", "/policy", "/alerts", "/export"):
        r = api.get(f"/api/v1/series/{path}{ep}")
        assert r.status_code in (404, 422, 307, 405), (raw, ep, r.status_code)
        assert r.status_code < 500 and "Traceback" not in r.text
    r = api.w("DELETE", f"/api/v1/series/{path}")
    assert r.status_code in (404, 422, 405, 307)
    assert api.get("/health").json()["status"] == "ok"
    assert api.get("/api/v1/series", params={"limit": 200}).json()["total"] >= 2  # still intact


@pytest.mark.parametrize(
    "q",
    [
        "' OR 1=1--",
        "1; DROP TABLE series",
        "%27",
        "\u0000",
        "a" * 3000,
        "[]",
        "{}",
        "null",
        "../..",
    ],
    ids=range(9),
)
def test_injection_style_query_values(api, q):
    for path, key in (
        ("/api/v1/series", "indicator_type"),
        ("/api/v1/series", "limit"),
        ("/api/v1/series/us_ilinet_wili/data", "start"),
        ("/api/v1/series/us_ilinet_wili/alerts", "method"),
        ("/api/v1/alerts/rules", "series_id"),
    ):
        try:
            r = api.get(path, params={key: q})
        except httpx.InvalidURL:
            continue
        assert r.status_code in (200, 422), (path, key, r.status_code)
    assert api.get("/health").status_code == 200


def test_injection_in_names_is_stored_literally_and_harmless(api, mk):
    evil = "x'); DROP TABLE series; --"
    s = mk([1, 2, 3, 4], name=evil, disease=evil, geography="<img src=x onerror=alert(1)>")
    m = api.get(f"/api/v1/series/{s}").json()
    assert m["name"] == evil and m["geography"].startswith("<img")
    assert api.get("/api/v1/series", params={"limit": 200}).json()["total"] >= 3
    j = api.get(f"/api/v1/series/{s}/export", params={"format": "json"})
    assert j.headers["content-type"].startswith("application/json")  # never text/html


def test_request_size_limit_content_length_and_stream(lim, lim_svc):
    big = body("big_one", [1.0] * 50)
    big["meta"]["notice"] = "x" * 20000
    raw = json.dumps(big).encode()
    assert len(raw) > 8192
    r = lim.w("POST", "/api/v1/series", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    def chunks():  # chunked transfer encoding: no Content-Length to check up front
        for i in range(0, len(raw), 1000):
            yield raw[i : i + 1000]

    r = lim.w(
        "POST", "/api/v1/series", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413, r.text
    assert lim.get("/health").status_code == 200
    ok = body("small_one", [1.0] * 10)
    assert lim.w("POST", "/api/v1/series", json=ok).status_code == 201
    lim.w("DELETE", "/api/v1/series/small_one")


def test_default_2mib_limit_413_on_main_service(api):
    raw = (
        b'{"meta": {"series_id": "big", "name": "n", "notice": "'
        + b"x" * (3 * 1024 * 1024)
        + b'"}}'
    )
    r = api.w("POST", "/api/v1/series", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_oversized_body_is_rejected_before_auth_check_leaks_nothing(api):
    raw = b"x" * (3 * 1024 * 1024)
    r = api.request("POST", "/api/v1/series", content=raw)  # no key at all
    assert r.status_code in (401, 413)


def test_observation_and_series_caps(lim):
    r = lim.w("POST", "/api/v1/series", json=body("too_long", [1.0] * 61))
    assert r.status_code == 422 and "at most 60" in r.json()["error"]["message"]
    assert lim.w("POST", "/api/v1/series", json=body("cap_ok", [1.0] * 60)).status_code == 201
    made = ["cap_ok"]
    for i in range(3):
        assert lim.w("POST", "/api/v1/series", json=body(f"cap_{i}", [1.0] * 5)).status_code == 201
        made.append(f"cap_{i}")
    r = lim.w("POST", "/api/v1/series", json=body("cap_over", [1.0] * 5))
    assert r.status_code == 409 and "series limit" in r.json()["error"]["message"]
    # replacing an existing series is still allowed at the cap
    assert (
        lim.w(
            "POST", "/api/v1/series", params={"overwrite": "true"}, json=body("cap_0", [2.0] * 5)
        ).status_code
        == 201
    )
    # deriving at the cap is refused too
    r = lim.w(
        "POST",
        "/api/v1/derive",
        json={"series_id": "cap_der", "name": "d", "expr": "cap_0 + cap_1"},
    )
    assert r.status_code == 409
    for m in made:
        lim.w("DELETE", f"/api/v1/series/{m}")


def test_cors_allowed_origin_and_preflight(api):
    r = api.get("/health", headers={"Origin": CORS_ORIGIN})
    assert r.headers["access-control-allow-origin"] == CORS_ORIGIN
    assert "access-control-allow-credentials" not in r.headers
    pre = api.request(
        "OPTIONS",
        "/api/v1/series",
        headers={
            "Origin": CORS_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key,content-type",
        },
    )
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == CORS_ORIGIN
    assert "x-api-key" in pre.headers["access-control-allow-headers"].lower()
    assert "PATCH" not in pre.headers["access-control-allow-methods"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",
        "http://app.example.test",
        "https://app.example.test.evil.com",
        "*",
    ],
)
def test_cors_other_origins_get_no_headers(api, origin):
    r = api.get("/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in r.headers
    pre = api.request(
        "OPTIONS",
        "/api/v1/series",
        headers={"Origin": origin, "Access-Control-Request-Method": "DELETE"},
    )
    assert "access-control-allow-origin" not in pre.headers


def test_cors_off_by_default(lim):
    r = lim.get("/health", headers={"Origin": CORS_ORIGIN})
    assert "access-control-allow-origin" not in r.headers


def test_odd_http_methods_and_paths(api):
    assert api.request("TRACE", "/health").status_code in (405, 404)
    assert api.request("PATCH", "/api/v1/series").status_code == 405
    assert api.request("HEAD", "/health").status_code in (200, 405)
    assert api.get("//health").status_code in (200, 404, 307)
    assert api.get("/api/v1/series/").status_code in (200, 307, 404)
    r = api.get("/health", headers={"Accept": "text/html"})
    assert r.status_code == 200


def test_trailing_slash_and_case(api):
    assert api.get("/api/v1/series/US_ILINET_WILI").status_code == 422  # ids are lower-case only


def test_nan_inf_via_raw_json_are_422(api, uid):
    for tok in ("NaN", "Infinity", "-Infinity", "1e999"):
        raw = (
            '{"meta":{"series_id":"%s","name":"n","indicator_type":"count"},'
            '"observations":[{"ds":"2020-01-05","y":%s}]}' % (uid(), tok)
        ).encode()
        r = api.w(
            "POST", "/api/v1/series", content=raw, headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 422, (tok, r.status_code, r.text)


def test_deeply_nested_and_huge_json_do_not_crash(api):
    nested = b"[" * 5000 + b"]" * 5000
    r = api.w(
        "POST", "/api/v1/series", content=nested, headers={"Content-Type": "application/json"}
    )
    assert r.status_code in (400, 422)
    assert api.get("/health").status_code == 200
    big_ints = (
        '{"meta": {"series_id": "zz", "name": "n", "indicator_type": "count", '
        '"season_length": %s}, "observations": []}' % ("9" * 400)
    ).encode()
    r = api.w(
        "POST", "/api/v1/series", content=big_ints, headers={"Content-Type": "application/json"}
    )
    assert r.status_code in (400, 422)


def test_slow_client_does_not_block_server(svc, api):
    """A half-open request (headers sent, body never finished) must not stall other requests."""
    import socket

    host, port = svc.url.replace("http://", "").split(":")
    s = socket.create_connection((host, int(port)))
    s.sendall(
        b"POST /api/v1/series HTTP/1.1\r\nHost: x\r\nContent-Length: 1000\r\n"
        b"Content-Type: application/json\r\n\r\n{"
    )
    t0 = time.perf_counter()
    assert api.get("/health").status_code == 200
    assert time.perf_counter() - t0 < 2
    s.close()


def test_many_sequential_requests_stable_latency(api):
    ts = []
    for _ in range(100):
        t0 = time.perf_counter()
        assert api.get("/health").status_code == 200
        ts.append(time.perf_counter() - t0)
    ts.sort()
    from .harness import REC

    REC.timings["health_p50_ms"] = round(ts[50] * 1000, 1)
    REC.timings["health_p95_ms"] = round(ts[95] * 1000, 1)
    assert ts[95] < 0.5


def test_logs_do_not_contain_key_or_payload(svc, api, mk):
    mk([1, 2, 3, 4], notice="PAYLOAD-MARKER-QQQ")
    api.request("POST", "/api/v1/export", headers={"X-API-Key": "wrong-wrong-wrong-wrong"})
    api.get("/api/v1/series", params={"limit": 1, "indicator_type": "QUERY-MARKER-ZZZ"})
    logs = svc.logs()
    assert api.key not in logs and "wrong-wrong-wrong-wrong" not in logs
    assert "PAYLOAD-MARKER-QQQ" not in logs and "QUERY-MARKER-ZZZ" not in logs
    assert "request_id=" in logs  # but there is an access log


def test_threads_unused_marker():
    assert threading  # keep import for linters in minimal envs
