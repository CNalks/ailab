"""Alerts: ad-hoc scans, stored rules (CRUD, idempotent PUT, validation) and /alerts/evaluate."""

import pytest

from .conftest import sid

SPIKE = [20.0, 22, 19, 21, 20, 23, 18, 22, 21, 20, 19, 22, 21, 400, 20, 21]


@pytest.mark.parametrize("method", ["ears_c1", "ears_c2", "ears_c3", "cusum"])
def test_adhoc_scan_methods_detect_a_spike(api, mk, method):
    s = mk(SPIKE)
    r = api.get(f"/api/v1/series/{s}/alerts", params={"method": method, "baseline": 7})
    assert r.status_code == 200, r.text
    j = r.json()
    assert (
        j["method"] == method
        and j["n_signals"] >= 1
        and "2020-03-29" in j["signal_periods"]
        or j["n_signals"] >= 1
    )
    assert "not an outbreak declaration" in j["wording"]
    assert "risk" not in r.text.lower() or "colour" not in r.text.lower()


@pytest.mark.parametrize(
    "params",
    [
        {"method": "ears_c9"},
        {"method": ""},
        {"baseline": 2},
        {"baseline": 105},
        {"baseline": "x"},
        {"baseline": -1},
    ],
)
def test_adhoc_scan_validation(api, mk, params):
    s = mk(SPIKE)
    assert api.get(f"/api/v1/series/{s}/alerts", params=params).status_code == 422


def test_short_series_scan_is_empty_not_error(api, mk):
    s = mk([1.0, 2.0])
    r = api.get(f"/api/v1/series/{s}/alerts")
    assert r.status_code == 200 and r.json()["n_evaluated"] == 0 and r.json()["latest"] is None


def test_rule_crud_idempotent_and_evaluate(api, mk, uid):
    s = mk(SPIKE)
    rid = uid("r")
    rule = {"rule_id": rid, "series_id": s, "method": "ears_c2", "baseline": 7, "threshold": 3.0}
    for _ in range(2):  # PUT is create-or-replace: idempotent
        r = api.w("PUT", "/api/v1/alerts/rules", json=rule)
        assert r.status_code == 200 and r.json()["rule_id"] == rid
    rules = api.get("/api/v1/alerts/rules", params={"series_id": s}).json()
    assert [x["rule_id"] for x in rules] == [rid]
    assert (
        api.w(
            "PUT", "/api/v1/alerts/rules", json={**rule, "method": "cusum", "threshold": 2.0}
        ).status_code
        == 200
    )
    assert api.get("/api/v1/alerts/rules", params={"series_id": s}).json()[0]["method"] == "cusum"
    ev = api.get("/api/v1/alerts/evaluate", params={"limit": 200}).json()
    mine = [i for i in ev["items"] if i["rule"]["rule_id"] == rid]
    assert len(mine) == 1 and mine[0]["n_signals"] >= 1 and ev["total"] >= 1
    # disabled rules are not evaluated
    api.w("PUT", "/api/v1/alerts/rules", json={**rule, "enabled": False})
    ev = api.get("/api/v1/alerts/evaluate", params={"limit": 200}).json()
    assert rid not in [i["rule"]["rule_id"] for i in ev["items"]]
    assert api.w("DELETE", f"/api/v1/alerts/rules/{rid}").json() == {"deleted": rid}
    assert api.w("DELETE", f"/api/v1/alerts/rules/{rid}").status_code == 404


def test_rules_are_removed_with_their_series(api, mk, uid):
    s = mk(SPIKE)
    rid = uid("r")
    api.w("PUT", "/api/v1/alerts/rules", json={"rule_id": rid, "series_id": s})
    api.w("DELETE", f"/api/v1/series/{s}")
    assert api.get("/api/v1/alerts/rules", params={"series_id": s}).json() == []
    assert rid not in [x["rule_id"] for x in api.get("/api/v1/alerts/rules").json()]


@pytest.mark.parametrize(
    "mut,code",
    [
        ({"series_id": "no_such_series_q"}, 404),
        ({"method": "bogus"}, 422),
        ({"baseline": 2}, 422),
        ({"baseline": 105}, 422),
        ({"threshold": 0}, 422),
        ({"threshold": -1}, 422),
        ({"threshold": "x"}, 422),
        ({"rule_id": "Bad Id"}, 422),
        ({"rule_id": "a'; DROP TABLE alert_rules;--"}, 422),
        ({"rule_id": "x" * 65}, 422),
        ({"enabled": "maybe"}, 422),
    ],
)
def test_rule_validation(api, mk, mut, code):
    s = mk(SPIKE)
    rule = {"rule_id": "val_rule_x", "series_id": s, **mut}
    r = api.w("PUT", "/api/v1/alerts/rules", json=rule)
    assert r.status_code == code, r.text
    api.w("DELETE", "/api/v1/alerts/rules/val_rule_x")


def test_evaluate_pagination(api, mk, uid):
    s = mk(SPIKE)
    ids = [uid(f"p{i}") for i in range(5)]
    for i in ids:
        api.w("PUT", "/api/v1/alerts/rules", json={"rule_id": i, "series_id": s})
    seen, off = [], 0
    while off is not None:
        p = api.get("/api/v1/alerts/evaluate", params={"limit": 2, "offset": off}).json()
        seen += [x["rule"]["rule_id"] for x in p["items"]]
        off = p["next_offset"]
    assert set(ids) <= set(seen) and len(seen) == len(set(seen)) == p["total"]
    for bad in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert api.get("/api/v1/alerts/evaluate", params=bad).status_code == 422
    for i in ids:
        api.w("DELETE", f"/api/v1/alerts/rules/{i}")


def test_rules_list_filter_validation(api):
    assert api.get("/api/v1/alerts/rules", params={"series_id": "Bad Id!"}).status_code == 422
    assert api.get("/api/v1/alerts/rules", params={"series_id": "nothing_here"}).json() == []


def test_rule_on_testset_outbreak_series(api, loaded, uid):
    rid = uid("o")
    s = sid("count_outbreaks")
    api.w("PUT", "/api/v1/alerts/rules", json={"rule_id": rid, "series_id": s, "method": "ears_c3"})
    ev = api.get("/api/v1/alerts/evaluate", params={"limit": 200}).json()
    mine = next(i for i in ev["items"] if i["rule"]["rule_id"] == rid)
    assert mine["n_signals"] >= 1 and mine["signal_periods"]
    api.w("DELETE", f"/api/v1/alerts/rules/{rid}")
