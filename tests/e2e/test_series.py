"""Series CRUD, data pagination, export formats, quality, lineage, idempotency, validation."""

import io
import json

import pandas as pd
import pytest

from .conftest import body

YS = [10.0, 12.0, None, 15.0, 20.0, 18.0, 25.0, 30.0]


def test_create_get_data_roundtrip_preserves_nulls(api, mk):
    s = mk(YS, name="Round trip", disease="d", unit="u")
    m = api.get(f"/api/v1/series/{s}").json()
    assert m["series_id"] == s and m["indicator_type"] == "count" and m["name"] == "Round trip"
    assert m["created_at"] and m["frequency"] == "W" and m["derived_expr"] is None
    d = api.get(f"/api/v1/series/{s}/data").json()
    assert [o["y"] for o in d["items"]] == YS
    assert d["total"] == len(YS) and d["next_offset"] is None and d["indicator_type"] == "count"
    # dates ascending, weekly
    ds = pd.to_datetime([o["ds"] for o in d["items"]])
    assert (pd.Series(ds).diff().dropna() == pd.Timedelta(days=7)).all()


def test_unsorted_input_is_sorted(api, uid):
    s = uid()
    b = body(s, [1, 2, 3, 4])
    b["observations"].reverse()
    assert api.w("POST", "/api/v1/series", json=b).status_code == 201
    d = api.get(f"/api/v1/series/{s}/data").json()["items"]
    assert [o["y"] for o in d] == [1, 2, 3, 4]
    api.w("DELETE", f"/api/v1/series/{s}")


def test_create_response_shape_and_201(api, uid):
    s = uid()
    r = api.w("POST", "/api/v1/series", json=body(s, [1, 2, 3]))
    assert r.status_code == 201
    assert r.json() == {
        "series_id": s,
        "observations": 3,
        "replaced": False,
        "stale_dependents": [],
    }
    api.w("DELETE", f"/api/v1/series/{s}")


def test_conflict_without_overwrite_and_idempotent_overwrite(api, mk):
    s = mk([1, 2, 3, 4])
    b = body(s, [1, 2, 3, 4])
    r = api.w("POST", "/api/v1/series", json=b)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    first = api.get(f"/api/v1/series/{s}/data").json()
    for _ in range(3):  # idempotent: same request, same final state
        r = api.w("POST", "/api/v1/series", params={"overwrite": "true"}, json=b)
        assert r.status_code == 201 and r.json()["replaced"] is True
    assert api.get(f"/api/v1/series/{s}/data").json() == first
    assert api.get("/api/v1/series", params={"limit": 200}).json()["items"].count  # still lists
    ids = [m["series_id"] for m in api.get("/api/v1/series", params={"limit": 200}).json()["items"]]
    assert ids.count(s) == 1


def test_overwrite_replaces_all_observations(api, mk):
    s = mk([1, 2, 3, 4, 5, 6])
    api.w("POST", "/api/v1/series", params={"overwrite": "true"}, json=body(s, [9, 9]))
    d = api.get(f"/api/v1/series/{s}/data").json()
    assert d["total"] == 2 and [o["y"] for o in d["items"]] == [9, 9]


def test_delete_idempotency(api, mk):
    s = mk([1, 2, 3])
    assert api.w("DELETE", f"/api/v1/series/{s}").json() == {"deleted": [s]}
    assert api.w("DELETE", f"/api/v1/series/{s}").status_code == 404
    assert api.get(f"/api/v1/series/{s}").status_code == 404
    assert api.get(f"/api/v1/series/{s}/data").status_code == 404


@pytest.mark.parametrize(
    "ep",
    [
        "",
        "/data",
        "/observations",
        "/quality",
        "/lineage",
        "/forecast",
        "/policy",
        "/alerts",
        "/export",
    ],
)
def test_unknown_series_is_404_everywhere(api, ep):
    r = api.get(f"/api/v1/series/no_such_series_xyz{ep}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_list_pagination_and_filters(api, mk):
    ids = [mk([1, 2, 3, 4], kind="count", tag="c") for _ in range(2)] + [
        mk([0.1, 0.2, 0.3, 0.4], kind="proportion", tag="p")
    ]
    p = api.get("/api/v1/series", params={"limit": 2, "offset": 0}).json()
    assert len(p["items"]) == 2 and p["limit"] == 2 and p["total"] >= 5 and p["next_offset"] == 2
    seen, off = [], 0
    while off is not None:  # walk all pages: no duplicates, no gaps
        p = api.get("/api/v1/series", params={"limit": 3, "offset": off}).json()
        seen += [m["series_id"] for m in p["items"]]
        off = p["next_offset"]
    assert len(seen) == len(set(seen)) == p["total"] and set(ids) <= set(seen)
    only_p = api.get("/api/v1/series", params={"indicator_type": "proportion", "limit": 200}).json()
    assert ids[2] in [m["series_id"] for m in only_p["items"]]
    assert all(m["indicator_type"] == "proportion" for m in only_p["items"])
    d = api.get("/api/v1/series", params={"derived": "true", "limit": 200}).json()
    assert all(m["derived_expr"] for m in d["items"])
    b = api.get("/api/v1/series", params={"derived": "false", "limit": 200}).json()
    assert all(m["derived_expr"] is None for m in b["items"])
    far = api.get("/api/v1/series", params={"offset": 10_000}).json()
    assert far["items"] == [] and far["next_offset"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"limit": -1},
        {"limit": "x"},
        {"limit": 1.5},
        {"offset": -1},
        {"offset": "x"},
        {"indicator_type": "bogus"},
        {"derived": "maybe"},
        {"limit": 10**30},
    ],
)
def test_list_param_validation(api, params):
    r = api.get("/api/v1/series", params=params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert "details" in r.json()["error"]


def test_data_pagination_limits_and_range_filters(api, mk):
    s = mk([float(i) for i in range(30)])
    assert api.get(f"/api/v1/series/{s}/data", params={"limit": 5000}).status_code == 200
    for bad in (
        {"limit": 0},
        {"limit": 5001},
        {"offset": -3},
        {"start": "2020-13-01"},
        {"end": "not-a-date"},
        {"start": "2020-02-30"},
        {"limit": "NaN"},
    ):
        r = api.get(f"/api/v1/series/{s}/data", params=bad)
        assert r.status_code == 422, bad
    p = api.get(f"/api/v1/series/{s}/data", params={"limit": 10, "offset": 25}).json()
    assert len(p["items"]) == 5 and p["next_offset"] is None and p["total"] == 30
    p = api.get(
        f"/api/v1/series/{s}/data", params={"start": "2020-02-02", "end": "2020-03-01"}
    ).json()
    assert [o["ds"] for o in p["items"]] == [
        "2020-02-02",
        "2020-02-09",
        "2020-02-16",
        "2020-02-23",
        "2020-03-01",
    ]
    assert p["total"] == 5
    empty = api.get(f"/api/v1/series/{s}/data", params={"start": "2030-01-01"}).json()
    assert empty["items"] == [] and empty["total"] == 0
    inv = api.get(
        f"/api/v1/series/{s}/data", params={"start": "2021-01-01", "end": "2020-01-01"}
    ).json()
    assert inv["items"] == []
    alias = api.get(f"/api/v1/series/{s}/observations", params={"limit": 3}).json()
    assert len(alias["items"]) == 3


def test_export_csv_json_parquet(api, mk):
    s = mk(YS)
    csv = api.get(f"/api/v1/series/{s}/export")
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    assert s in csv.headers["content-disposition"]
    df = pd.read_csv(io.StringIO(csv.text))
    assert list(df.columns) == ["series_id", "period_start", "value"] and len(df) == len(YS)
    assert df["value"].isna().sum() == 1  # missing stays empty, never 0
    js = api.get(f"/api/v1/series/{s}/export", params={"format": "json"}).json()
    assert js["series"]["series_id"] == s and [o["value"] for o in js["observations"]][2] is None
    pq = api.get(f"/api/v1/series/{s}/export", params={"format": "parquet"})
    assert pq.content[:4] == b"PAR1"
    pdf = pd.read_parquet(io.BytesIO(pq.content))
    assert len(pdf) == len(YS) and pdf["value"].isna().sum() == 1
    assert api.get(f"/api/v1/series/{s}/export", params={"format": "xlsx"}).status_code == 422


def test_quality_and_lineage_of_base_series(api, mk):
    s = mk(YS)
    q = api.get(f"/api/v1/series/{s}/quality").json()
    assert "checks" in q and "grade" in q and q["data_through"] == "2020-02-23"
    lin = api.get(f"/api/v1/series/{s}/lineage").json()
    assert lin["is_derived"] is False and lin["dependents"] == [] and lin["derived_from"] == []


def test_unicode_roundtrip(api, mk):
    name = '流感样病例 — ILI ✓ "q" <b>x</b> é\u0301 🌏'
    s = mk([1, 2, 3, 4], name=name, disease="呼吸道", geography="示例", unit="例/周")
    m = api.get(f"/api/v1/series/{s}").json()
    assert m["name"] == name and m["disease"] == "呼吸道"
    js = api.get(f"/api/v1/series/{s}/export", params={"format": "json"})
    assert json.loads(js.content)["series"]["name"] == name
    assert api.get(f"/api/v1/series/{s}/export").status_code == 200


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda b: b.pop("observations"), 422),
        (lambda b: b.pop("meta"), 422),
        (lambda b: b["meta"].pop("name"), 422),
        (lambda b: b["meta"].update(frequency="D"), 422),
        (lambda b: b["meta"].update(value_scale=10), 422),
        (lambda b: b["meta"].update(season_length=61), 422),
        (lambda b: b["meta"].update(season_length="x"), 422),
        (lambda b: b["observations"][0].update(ds=None), 422),
        (lambda b: b["observations"][0].update(ds=20240107), 422),
        (lambda b: b["observations"][0].update(ds="2024-01-07T10:30:00"), 422),
        (lambda b: b["observations"][0].update(y=True), 201),  # pydantic coerces; documented below
        (lambda b: b.update(observations={"ds": "2024-01-07", "y": 1}), 422),
        (lambda b: b.update(extra_field=1), 201),
    ],
)
def test_create_validation_matrix(api, uid, mutate, code):
    s = uid()
    b = body(s, [1, 2, 3, 4])
    mutate(b)
    r = api.w("POST", "/api/v1/series", json=b)
    assert r.status_code == code, r.text
    if code == 422:
        assert r.json()["error"]["code"] in ("validation_error",)
    api.w("DELETE", f"/api/v1/series/{s}")


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b"null", b"[]", b'"str"', b"{'a':1}", b'{"meta":', b"\xff\xfe\x00", b"1" * 5000],
    ids=[
        "empty",
        "open_brace",
        "null",
        "list",
        "string",
        "single_quotes",
        "truncated",
        "bad_utf8",
        "5000_digits",
    ],
)
def test_malformed_json_bodies(api, raw):
    r = api.w("POST", "/api/v1/series", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422), r.text
    assert r.json()["error"]["code"] in ("bad_request", "validation_error")
    assert "Traceback" not in r.text


def test_wrong_content_type(api, uid):
    s = uid()
    r = api.w(
        "POST",
        "/api/v1/series",
        content=json.dumps(body(s, [1, 2, 3])),
        headers={"Content-Type": "text/plain"},
    )
    assert r.status_code == 422
    r = api.w("POST", "/api/v1/series", data={"a": "b"})
    assert r.status_code == 422


def test_validation_errors_never_echo_input(api, uid):
    secret = "SUPER-SECRET-PAYLOAD-ZZZ"
    b = body(uid(), [1, 2, 3, 4])
    b["observations"][1]["y"] = secret
    r = api.w("POST", "/api/v1/series", json=b)
    assert r.status_code == 422 and secret not in r.text
