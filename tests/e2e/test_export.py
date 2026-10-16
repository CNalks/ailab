"""POST /api/v1/export (server-side static export) and per-series download edge cases."""

import pytest


def test_static_export_writes_manifest(api, svc, mk):
    mk([float(i % 7 + 1) for i in range(160)], tag="x")
    r = api.w("POST", "/api/v1/export")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["series"] and all("policy" in s and "quality_grade" in s for s in m["series"])
    ids = [s["series_id"] for s in m["series"]]
    assert "us_ilinet_wili" in ids and len(ids) == len(set(ids))


def test_export_is_idempotent(api):
    a = api.w("POST", "/api/v1/export").json()
    b = api.w("POST", "/api/v1/export").json()
    assert [s["series_id"] for s in a["series"]] == [s["series_id"] for s in b["series"]]


def test_export_forbidden_when_readonly(ro):
    assert ro.post("/api/v1/export").status_code == 403


@pytest.mark.parametrize("fmt", ["csv", "json", "parquet"])
def test_download_headers(api, mk, fmt):
    s = mk([1, 2, 3, 4])
    r = api.get(f"/api/v1/series/{s}/export", params={"format": fmt})
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="{s}.{fmt}"'


@pytest.mark.parametrize("fmt", ["CSV", "xml", "", "csv;ls", "../x"])
def test_download_bad_format(api, mk, fmt):
    s = mk([1, 2, 3, 4])
    assert api.get(f"/api/v1/series/{s}/export", params={"format": fmt}).status_code == 422
