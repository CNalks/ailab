"""POST /api/v1/export (server-side static export) and per-series download edge cases."""

import time

import pytest

from .conftest import body
from .harness import REC, Api, Instance, KEY


@pytest.fixture(scope="module")
def exp(workdir):
    """Own instance without demo data: /export analyses *every* series, keep it small."""
    inst = Instance("exp", {"AILAB_API_KEY": KEY}, workdir)
    with Api(inst.url) as c:
        for i in range(2):
            ys = [float((j * 7 + i) % 23 + 1) for j in range(160)]
            assert c.w("POST", "/api/v1/series", json=body(f"exp_{i}", ys)).status_code == 201
        yield c
    inst.close()


def test_static_export_writes_manifest(exp):
    t0 = time.perf_counter()
    r = exp.w("POST", "/api/v1/export")
    REC.timings["export_2_series_s"] = round(time.perf_counter() - t0, 2)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["series"] and all("policy" in s and "quality_grade" in s for s in m["series"])
    ids = [s["series_id"] for s in m["series"]]
    assert ids == ["exp_0", "exp_1"] and m["static_precomputed"] is True


def test_export_is_idempotent(exp):
    a = exp.w("POST", "/api/v1/export").json()
    b = exp.w("POST", "/api/v1/export").json()
    assert [s["sha256"] for s in a["series"]] == [s["sha256"] for s in b["series"]]


@pytest.mark.slow
def test_export_of_whole_testset_completes(api, loaded):
    t0 = time.perf_counter()
    r = api.w("POST", "/api/v1/export")
    REC.timings["export_whole_testset_s"] = round(time.perf_counter() - t0, 1)
    assert r.status_code in (200, 422), r.text  # 422 = size budget exceeded (structured), never 5xx


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
