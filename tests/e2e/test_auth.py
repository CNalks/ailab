"""Auth model: reads open; every write endpoint needs X-API-Key; no key => read-only (403)."""

import pytest

from .conftest import body

WRITES = [
    ("POST", "/api/v1/series"),
    ("DELETE", "/api/v1/series/does_not_exist"),
    ("POST", "/api/v1/derive"),
    ("POST", "/api/v1/series/does_not_exist/recompute"),
    ("PUT", "/api/v1/alerts/rules"),
    ("DELETE", "/api/v1/alerts/rules/does_not_exist"),
    ("POST", "/api/v1/export"),
]
READS = [
    "/health",
    "/ready",
    "/docs",
    "/openapi.json",
    "/api/v1/series",
    "/api/v1/series/us_ilinet_wili",
    "/api/v1/series/us_ilinet_wili/data?limit=3",
    "/api/v1/series/us_ilinet_wili/quality",
    "/api/v1/series/us_ilinet_wili/lineage",
    "/api/v1/series/us_ilinet_wili/export?format=csv",
    "/api/v1/series/us_ilinet_wili/alerts",
    "/api/v1/alerts/rules",
    "/api/v1/alerts/evaluate",
]
BAD_KEYS = {
    "wrong_same_length": "x" * len("e2e-key-0123456789abcdef"),
    "short": "abc",
    "empty": "",
    "prefix_of_real": "e2e-key-0123456789abcde",
    "real_plus_char": "e2e-key-0123456789abcdefX",
    "case_changed": "E2E-KEY-0123456789ABCDEF",
    "unicode": "密钥密钥密钥密钥密钥密钥密钥密钥密钥密钥",
}


def _payload(method, path):
    if method == "POST" and path == "/api/v1/series":
        return {"json": body("auth_probe", [1, 2, 3])}
    if path == "/api/v1/derive":
        return {"json": {"series_id": "auth_probe", "name": "n", "expr": "a + b"}}
    if method == "PUT":
        return {"json": {"rule_id": "auth_probe", "series_id": "us_ilinet_wili"}}
    return {}


@pytest.mark.parametrize("method,path", WRITES)
def test_write_without_key_is_401(api, method, path):
    r = api.request(method, path, **_payload(method, path))
    assert r.status_code == 401, r.text
    assert r.json()["error"]["code"] == "unauthorized"
    assert "e2e-key" not in r.text


@pytest.mark.parametrize("name", list(BAD_KEYS))
@pytest.mark.parametrize("method,path", WRITES)
def test_write_with_bad_key_is_401(api, method, path, name):
    try:
        r = api.request(
            method, path, headers={"X-API-Key": BAD_KEYS[name]}, **_payload(method, path)
        )
    except Exception as exc:  # header encoding refused by the client itself (unicode)
        assert name == "unicode", exc
        return
    assert r.status_code == 401, (name, r.text)
    assert BAD_KEYS[name] not in r.text or not BAD_KEYS[name]


def test_key_in_query_or_cookie_or_bearer_is_not_accepted(api):
    k = api.key
    for kw in (
        {"params": {"api_key": k}},
        {"params": {"X-API-Key": k}},
        {"headers": {"Authorization": f"Bearer {k}"}},
        {"headers": {"Cookie": f"X-API-Key={k}"}},
    ):
        r = api.request("POST", "/api/v1/export", **kw)
        assert r.status_code == 401, kw


def test_key_header_name_is_case_insensitive(api):
    r = api.request("DELETE", "/api/v1/alerts/rules/nope_rule", headers={"x-api-key": api.key})
    assert r.status_code == 404  # authorised, then "unknown rule"


def test_duplicate_key_headers_do_not_bypass(api):
    r = api.request(
        "POST",
        "/api/v1/export",
        headers=[("X-API-Key", "wrong-wrong-wrong-wrong"), ("X-API-Key", api.key)],
    )
    assert r.status_code in (200, 401)  # either is safe; must never be a 5xx
    assert r.status_code < 500


@pytest.mark.parametrize("path", READS)
def test_reads_need_no_key(api, path):
    assert api.get(path).status_code == 200


@pytest.mark.parametrize("method,path", WRITES)
def test_readonly_service_rejects_writes_even_with_a_key(ro, method, path):
    for hdr in ({}, {"X-API-Key": "e2e-key-0123456789abcdef"}, {"X-API-Key": "x" * 40}):
        r = ro.request(method, path, headers=hdr, **_payload(method, path))
        assert r.status_code == 403, (hdr, r.text)
        assert r.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("path", READS)
def test_readonly_service_serves_reads(ro, path):
    assert ro.get(path).status_code == 200


def test_readonly_derive_preview_still_works(ro):
    r = ro.post(
        "/api/v1/derive/preview",
        json={
            "series_id": "pv",
            "name": "preview",
            "expr": "rolling_mean(us_ilinet_ili_visits, 4)",
        },
    )
    assert r.status_code == 200 and r.json()["stored"] is False


def test_auth_checked_before_validation_and_existence(api):
    # an unauthenticated caller must not learn whether a series exists
    a = api.request("DELETE", "/api/v1/series/us_ilinet_wili")
    b = api.request("DELETE", "/api/v1/series/definitely_missing")
    assert a.status_code == b.status_code == 401
    assert api.get("/api/v1/series/us_ilinet_wili").status_code == 200  # nothing deleted
