"""Derived series: create/preview/recompute/lineage, conflicts, cascade, and expression safety."""

import re
import time

import pytest

from .conftest import body

POS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
TESTS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120]


@pytest.fixture
def pair(mk):
    return mk(POS, tag="pos"), mk(TESTS, tag="tst")


def dv(api, uid, expr, bindings=None, **kw):
    b = {"series_id": uid(), "name": "d", "expr": expr, **kw}
    if bindings:
        b["bindings"] = bindings
    return api.w("POST", "/api/v1/derive", json=b), b["series_id"]


def test_ratio_becomes_proportion_with_lineage(api, uid, pair):
    pos, tst = pair
    r, d = dv(api, uid, f"{pos} / {tst}")
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["stored"] is True and j["inferred_indicator_type"] == "proportion"
    assert j["observations"] == 12 and j["n_missing"] == 0
    data = api.get(f"/api/v1/series/{d}/data").json()["items"]
    assert all(abs(o["y"] - 0.1) < 1e-9 for o in data[:3])
    lin = api.get(f"/api/v1/series/{d}/lineage").json()
    assert lin["is_derived"] and lin["derived_from"] == sorted([pos, tst])
    assert lin["lineage"]["kind_inference"] and lin["lineage"]["alignment"]
    assert d in api.get(f"/api/v1/series/{pos}/lineage").json()["dependents"]
    assert api.get("/api/v1/series", params={"derived": "true", "limit": 200}).json()["total"] >= 1
    api.w("DELETE", f"/api/v1/series/{d}")


def test_percent_bindings_and_union_alignment(api, uid, mk):
    a = mk([1, 2, 3, 4], tag="a")
    b = mk([10, 20, 30, 40, 50, 60], tag="b")
    r, d = dv(api, uid, "100 * A / B", {"A": a, "B": b})
    assert r.status_code == 201, r.text
    assert r.json()["series"]["value_scale"] == 100 and r.json()["observations"] == 4
    r2, d2 = dv(api, uid, "A + B", {"A": a, "B": b}, align="union")
    # weeks outside A's range are missing (never 0); trailing missing weeks are trimmed + reported
    assert r2.status_code == 201 and r2.json()["observations"] == 4, r2.text
    assert r2.json()["series"]["lineage"]["alignment"]["trimmed_trailing_periods"] == 2
    assert all(o["y"] is not None for o in api.get(f"/api/v1/series/{d2}/data").json()["items"])
    for x in (d, d2):
        api.w("DELETE", f"/api/v1/series/{x}")


@pytest.mark.parametrize(
    "expr,kind",
    [
        ("A + B", "count"),
        ("A - B", "rate"),
        ("2 * A", "count"),
        ("rolling_sum(A, 3)", "count"),
        ("rolling_mean(A, 3)", "count"),
        ("rolling_ratio(A, B, 4)", "proportion"),
        ("sum(A, B)", "count"),
        ("lag(A, 2)", "count"),
        ("diff(A)", "rate"),
        ("pct_change(A, 1)", "rate"),
        ("abs(A)", "count"),
        ("sqrt(A)", "rate"),
        ("log1p(A)", "rate"),
        ("-A", "rate"),
    ],
)
def test_expression_kind_inference_matrix(api, uid, pair, expr, kind):
    binds = {k: v for k, v in zip("AB", pair, strict=True) if re.search(rf"\b{k}\b", expr)}
    r = api.post(
        "/api/v1/derive/preview",
        json={"series_id": uid(), "name": "p", "expr": expr, "bindings": binds},
    )
    assert r.status_code == 200, r.text
    assert r.json()["inferred_indicator_type"] == kind


def test_preview_stores_nothing_and_needs_no_key(api, uid, pair):
    s = uid()
    r = api.post(
        "/api/v1/derive/preview",
        json={"series_id": s, "name": "p", "expr": f"{pair[0]} / {pair[1]}"},
    )
    assert r.status_code == 200 and r.json()["stored"] is False and r.json()["sample"]
    assert api.get(f"/api/v1/series/{s}").status_code == 404


def test_division_by_zero_is_missing_not_inf(api, uid, mk):
    num = mk([1, 2, 3, 4], tag="n")
    den = mk([2, 0, 2, 2], tag="z")
    r, d = dv(api, uid, f"{num} / {den}")
    assert r.status_code == 201, r.text
    ys = [o["y"] for o in api.get(f"/api/v1/series/{d}/data").json()["items"]]
    assert ys == [0.5, None, 1.5, 2.0], ys
    assert r.json()["n_missing"] == 1
    api.w("DELETE", f"/api/v1/series/{d}")


def test_proportion_mean_is_refused(api, uid, pair):
    r, d = dv(api, uid, f"{pair[0]} / {pair[1]}")
    r2, _ = dv(api, uid, f"rolling_mean({d}, 4)")
    assert r2.status_code == 422 and r2.json()["error"]["code"] == "invalid_expression"
    api.w("DELETE", f"/api/v1/series/{d}")


def test_indicator_type_assertion(api, uid, pair):
    r, _ = dv(api, uid, f"{pair[0]} / {pair[1]}", indicator_type="count")
    assert r.status_code == 422
    r, d = dv(api, uid, f"{pair[0]} / {pair[1]}", indicator_type="proportion")
    assert r.status_code == 201
    api.w("DELETE", f"/api/v1/series/{d}")


def test_idempotency_conflict_and_overwrite(api, uid, pair):
    sid_ = uid()
    b = {"series_id": sid_, "name": "d", "expr": f"{pair[0]} + {pair[1]}"}
    assert api.w("POST", "/api/v1/derive", json=b).status_code == 201
    assert api.w("POST", "/api/v1/derive", json=b).status_code == 409
    first = api.get(f"/api/v1/series/{sid_}/data").json()
    for _ in range(2):
        assert api.w("POST", "/api/v1/derive", json={**b, "overwrite": True}).status_code == 201
    assert api.get(f"/api/v1/series/{sid_}/data").json() == first
    # a base series can never be replaced by a derivation
    r = api.w("POST", "/api/v1/derive", json={**b, "series_id": pair[0], "overwrite": True})
    assert r.status_code == 409
    # ... and a derived series can not be overwritten through the base-series endpoint
    r = api.w("POST", "/api/v1/series", params={"overwrite": "true"}, json=body(sid_, [1, 2, 3]))
    assert r.status_code == 409
    api.w("DELETE", f"/api/v1/series/{sid_}")


def test_cycle_and_self_reference(api, uid, pair):
    d = uid()
    b = {"series_id": d, "name": "d", "expr": f"{pair[0]} + 1"}
    assert api.w("POST", "/api/v1/derive", json=b).status_code == 201
    r = api.w(
        "POST",
        "/api/v1/derive",
        json={"series_id": d, "name": "x", "expr": f"{d} + 1", "overwrite": True},
    )
    assert r.status_code in (409, 422), r.text
    r = api.w(
        "POST",
        "/api/v1/derive",
        json={"series_id": pair[0], "name": "x", "expr": f"{d} + 1", "overwrite": True},
    )
    assert r.status_code in (409, 422)
    api.w("DELETE", f"/api/v1/series/{d}")


def test_recompute_follows_source_changes_and_is_idempotent(api, uid, mk):
    a = mk([1, 2, 3, 4, 5, 6], tag="a")
    r, d = dv(api, uid, f"2 * {a}")
    assert r.status_code == 201
    assert [o["y"] for o in api.get(f"/api/v1/series/{d}/data").json()["items"]][:2] == [2, 4]
    ow = api.w(
        "POST",
        "/api/v1/series",
        params={"overwrite": "true"},
        json=body(a, [10, 20, 30, 40, 50, 60]),
    )
    assert ow.json()["stale_dependents"] == [d]
    assert (
        api.get(f"/api/v1/series/{d}/data").json()["items"][0]["y"] == 2
    )  # snapshot until recompute
    for _ in range(2):
        rc = api.w("POST", f"/api/v1/series/{d}/recompute")
        assert rc.status_code == 200 and rc.json()["stored"] is True
    assert api.get(f"/api/v1/series/{d}/data").json()["items"][0]["y"] == 20
    assert api.w("POST", f"/api/v1/series/{a}/recompute").status_code in (409, 422)  # not derived
    assert api.w("POST", "/api/v1/series/missing_series/recompute").status_code == 404
    api.w("DELETE", f"/api/v1/series/{a}", params={"cascade": "true"})


def test_delete_source_conflict_then_cascade(api, uid, mk):
    a = mk([1, 2, 3, 4], tag="a")
    r, d = dv(api, uid, f"{a} + 1")
    assert r.status_code == 201
    dd = api.w("DELETE", f"/api/v1/series/{a}")
    assert dd.status_code == 409 and dd.json()["error"]["code"] == "has_dependents"
    assert dd.json()["error"]["details"]["dependents"] == [d]
    assert api.get(f"/api/v1/series/{d}").status_code == 200
    ok = api.w("DELETE", f"/api/v1/series/{a}", params={"cascade": "true"}).json()
    assert set(ok["deleted"]) == {a, d}
    assert api.get(f"/api/v1/series/{d}").status_code == 404


@pytest.mark.parametrize(
    "body_,code",
    [
        ({"series_id": "ok_id", "name": "n", "expr": ""}, 422),
        ({"series_id": "ok_id", "name": "", "expr": "a + 1"}, 422),
        ({"series_id": "Bad-ID", "name": "n", "expr": "a + 1"}, 422),
        ({"series_id": "ok_id", "name": "n", "expr": "a + 1", "align": "outer"}, 422),
        ({"series_id": "ok_id", "name": "n", "expr": "a + 1", "extra": 1}, 422),
        ({"series_id": "ok_id", "name": "n", "expr": "x" * 301}, 422),
        ({"series_id": "ok_id", "name": "n", "expr": "no_such_series + 1"}, (404, 422)),
        ({"series_id": "ok_id", "name": "n", "expr": "a +"}, 422),
        ({"series_id": "ok_id", "name": "n", "expr": "a + 1", "bindings": {"1bad": "x"}}, 422),
        (
            {"series_id": "ok_id", "name": "n", "expr": "a + 1", "bindings": {"A": "../etc"}},
            (404, 422),
        ),
    ],
)
def test_derive_validation(api, body_, code):
    r = api.post("/api/v1/derive/preview", json=body_)
    assert r.status_code in (code if isinstance(code, tuple) else (code,)), r.text
    assert "Traceback" not in r.text and r.json()["error"]["code"]


MALICIOUS = [
    "__import__('os').system('id')",
    "eval('1+1')",
    "exec('x=1')",
    "compile('1','','eval')",
    "open('/etc/passwd').read()",
    "().__class__.__bases__[0].__subclasses__()",
    "a.__class__",
    "a.s",
    "a.to_csv()",
    "a.to_pickle('/tmp/x')",
    "a.__dict__",
    "[x for x in range(10)]",
    "lambda: 1",
    "(lambda x: x)(1)",
    "a if 1 else 2",
    "a and 1",
    "not a",
    "a == a",
    "a < 1",
    "a[0]",
    "a[0:2]",
    "'str'",
    "'a' * 3",
    "b'x'",
    "f'{a}'",
    "{1: 2}",
    "{1}",
    "(1, 2)",
    "[1, 2]",
    "a := 1",
    "await a",
    "yield 1",
    "*a",
    "**a",
    "a @ a",
    "a // 2",
    "a % 2",
    "a ** 2",
    "2 ** a",
    "~a",
    "a << 2",
    "a | 1",
    "a & 1",
    "a ^ 1",
    "sum(a, b=1)",
    "sum(*[a])",
    "sum(**{})",
    "rolling_mean(a, '3')",
    "rolling_mean(a, a)",
    "rolling_mean(a, 0)",
    "rolling_mean(a, 521)",
    "rolling_mean(a, 1e9)",
    "rolling_mean(a, -1)",
    "rolling_mean(a, 2.5)",
    "lag(a, 10**9)",
    "getattr(a, 's')",
    "globals()",
    "locals()",
    "vars()",
    "dir(a)",
    "type(a)",
    "print(a)",
    "input()",
    "breakpoint()",
    "help()",
    "a.__init__.__globals__",
    "__builtins__",
    "os.system('id')",
    "sys.modules",
    "a;b",
    "a\nb",
    "a  # comment\n__import__('os')",
    "$(id)",
    "`id`",
    "a; import os",
    "1e999",
    "1e999 * a",
    "10**100000",
    "9**9**9",
    "99999999999999999999 * a",
    "a" * 400,
    "(" * 300 + "a" + ")" * 300,
    "-" * 200 + "a",
    "+".join(["a"] * 100),
    "sum(" * 40 + "a" + ")" * 40,
    "a +" * 80 + " 1",
    "",
    " ",
    "\u0000",
    "a\u0000b",
    "ａ + 1",
    "α + 1",
    "a + 1; DROP TABLE series",
    "a'; DROP TABLE series;--",
    "sum(a, a, a, a, a, a, a, a, a, a)",
]


@pytest.mark.parametrize("expr", MALICIOUS, ids=[f"m{i:03d}" for i in range(len(MALICIOUS))])
def test_malicious_expressions_are_rejected_fast_and_safely(api, mk, expr):
    a = mk([1, 2, 3, 4, 5, 6], tag="m")
    b = mk([2, 3, 4, 5, 6, 7], tag="m")
    t0 = time.perf_counter()
    binds = {k: v for k, v in (("a", a), ("b", b)) if re.search(rf"\b{k}\b", expr)}
    r = api.post(
        "/api/v1/derive/preview",
        json={"series_id": "mal_probe", "name": "x", "expr": expr, "bindings": binds},
    )
    dt_ = time.perf_counter() - t0
    assert dt_ < 5, f"slow ({dt_:.1f}s)"
    assert r.status_code in (404, 422), (expr[:60], r.status_code, r.text[:200])
    err = r.json()["error"]
    assert err["code"] in ("invalid_expression", "validation_error", "not_found", "alignment_error")
    assert "Traceback" not in r.text and "/home/" not in r.text and "uid=" not in r.text
    assert "bindings not used" not in err["message"], "rejected for the wrong reason"
    # the service is still alive and nothing was stored
    assert api.get("/health").status_code == 200
    assert api.get("/api/v1/series/mal_probe").status_code == 404


def test_expression_limits_are_enforced_not_just_documented(api, mk):
    a = mk(list(range(1, 30)), tag="x")
    ok = api.post(
        "/api/v1/derive/preview",
        json={
            "series_id": "lim_probe",
            "name": "x",
            "expr": "rolling_mean(a, 4)",
            "bindings": {"a": a},
        },
    )
    assert ok.status_code == 200
    # 8 sources max
    many = {f"S{i}": a for i in range(9)}
    r = api.post(
        "/api/v1/derive/preview",
        json={"series_id": "lim_probe", "name": "x", "expr": "S0 + S1", "bindings": many},
    )
    assert r.status_code == 422
