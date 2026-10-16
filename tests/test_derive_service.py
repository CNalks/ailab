"""Derived series end to end against the store: lineage, persistence, alignment, conflicts."""

import numpy as np
import pandas as pd
import pytest

from ailab import derive_service
from ailab.derive import AlignmentError, ExpressionError
from ailab.derive_service import DerivationConflict
from ailab.store import SeriesStore
from ailab.synth import synth_pair

from .conftest import SL, count_series, meta


@pytest.fixture
def pair_store(store):
    num, den = synth_pair(SL * 4, seed=3, season_length=SL)
    store.upsert_series(meta("pos", "count", disease="flu", geography="X", licence="L1"), num)
    store.upsert_series(
        meta("tests", "count", disease="flu", geography="X", licence="L2", source_id=""), den
    )
    return store


def test_count_ratio_creates_proportion_with_lineage(pair_store):
    d = derive_service.create_derived(pair_store, "pos_rate", "Positivity", "pos / tests")
    m = pair_store.get_meta("pos_rate")
    assert m.indicator_type == "proportion" and m.value_scale == 1 and m.unit == "proportion"
    assert m.derived_expr == "pos / tests" and m.derived_from == ["pos", "tests"]
    assert m.lineage["kind_inference"]["inferred"] == "proportion"
    assert [s["series_id"] for s in m.lineage["sources"]] == ["pos", "tests"]
    assert m.lineage["alignment"]["mode"] == "intersection"
    assert m.licence == "L1; L2" and "derived by ailab" in m.notice
    got = pair_store.get_observations("pos_rate")
    assert got["y"].between(0, 1).all() and len(got) == len(d.data)
    assert pair_store.dependents("pos") == ["pos_rate"]


def test_percent_alias_binding_and_assertion(pair_store):
    d = derive_service.create_derived(
        pair_store, "pct", "Pct", "100 * A / B", {"A": "pos", "B": "tests"}
    )
    assert d.meta.indicator_type == "proportion" and d.meta.value_scale == 100
    assert d.meta.unit == "percent" and d.meta.bindings == {"A": "pos", "B": "tests"}
    assert d.meta.derived_from == ["pos", "tests"]
    derive_service.create_derived(
        pair_store, "pct2", "Pct2", "pos / tests", indicator_type="proportion"
    )
    with pytest.raises(ExpressionError, match="infers"):
        derive_service.create_derived(
            pair_store, "bad", "Bad", "pos / tests", indicator_type="count"
        )
    assert not any(m.series_id == "bad" for m in pair_store.list_series())


def test_persistence_in_duckdb_file(tmp_path):
    st = SeriesStore(tmp_path / "d.duckdb")
    st.upsert_series(meta("a"), count_series(3, seed=1))
    st.upsert_series(meta("b"), count_series(3, seed=2))
    derive_service.create_derived(st, "s", "Sum", "sum(a, b)")
    st.close()
    st2 = SeriesStore(tmp_path / "d.duckdb")
    m = st2.get_meta("s")
    assert m.is_derived and m.derived_from == ["a", "b"] and m.lineage["expr"] == "sum(a, b)"
    assert st2.dependents("a") == ["s"] and len(st2.get_observations("s")) == SL * 3


def test_missing_inputs_propagate_to_null_not_zero(store):
    a = count_series(2, seed=1)
    b = count_series(2, seed=2)
    a.loc[5, "y"] = np.nan
    b = b.drop(index=[8])  # absent week
    store.upsert_series(meta("a"), a)
    store.upsert_series(meta("b"), b)
    d = derive_service.create_derived(store, "s", "S", "a + b")
    y = store.get_observations("s")["y"]
    assert y.isna().sum() == 2 and (y.dropna() > 0).all()
    assert d.meta.lineage["alignment"]["n_missing_result"] == 2
    assert any("missing" in w for w in d.warnings)


def test_leading_trailing_missing_are_trimmed_and_reported(store):
    store.upsert_series(meta("a"), count_series(2))
    d = derive_service.create_derived(store, "lg", "Lag", "lag(a, 4)")
    al = d.meta.lineage["alignment"]
    assert al["trimmed_leading_periods"] == 4 and len(d.data) == SL * 2 - 4
    assert d.data["y"].notna().all()


def test_union_vs_intersection(store):
    a = count_series(2, seed=1)
    b = count_series(2, seed=2).iloc[5:].reset_index(drop=True)
    store.upsert_series(meta("a"), a)
    store.upsert_series(meta("b"), b)
    inter = derive_service.plan(store, "x1", "x", "a + b")
    union = derive_service.plan(store, "x2", "x", "a + b", align="union")
    assert len(inter.data) == len(b)
    assert union.data["y"].isna().sum() == 5 or len(union.data) == len(b)  # leading NaN trimmed
    assert union.meta.lineage["alignment"]["mode"] == "union"
    assert inter.meta.lineage["alignment"]["per_source"]["a"]["n_outside_grid"] == 5


def test_misaligned_weekday_and_no_overlap_errors(store):
    store.upsert_series(meta("a"), count_series(2))
    monday = count_series(2, start="2012-01-02")
    store.upsert_series(meta("m"), monday)
    with pytest.raises(AlignmentError, match="same weekly grid"):
        derive_service.plan(store, "x", "x", "a + m")
    late = count_series(2, start="2020-01-05")
    store.upsert_series(meta("late"), late)
    with pytest.raises(AlignmentError, match="overlap"):
        derive_service.plan(store, "x", "x", "a + late")


def test_errors_and_conflicts(store):
    store.upsert_series(meta("a"), count_series(2))
    store.upsert_series(meta("b", season_length=SL + 1), count_series(2, seed=1))
    with pytest.raises(ExpressionError, match="unknown series"):
        derive_service.plan(store, "x", "x", "a + nope")
    with pytest.raises(ExpressionError, match="at least one series"):
        derive_service.plan(store, "x", "x", "1 + 1")
    with pytest.raises(ExpressionError, match="not used"):
        derive_service.plan(store, "x", "x", "a + 1", {"Z": "a"})
    with pytest.raises(ExpressionError, match="invalid alias"):
        derive_service.plan(store, "x", "x", "a + 1", {"__x": "a"})
    with pytest.raises(AlignmentError, match="season_length"):
        derive_service.plan(store, "x", "x", "a + b")
    with pytest.raises(DerivationConflict, match="itself"):
        derive_service.plan(store, "a", "x", "a * 2")
    with pytest.raises(DerivationConflict, match="already exists"):
        derive_service.create_derived(store, "a", "x", "a * 2")  # base series not overwritable
    with pytest.raises(ExpressionError, match="all missing"):
        derive_service.plan(store, "x", "x", "lag(a, 520)")


def test_overwrite_cycle_and_recompute(store):
    a = count_series(2, seed=1)
    store.upsert_series(meta("a"), a)
    derive_service.create_derived(store, "d1", "D1", "a * 2")
    derive_service.create_derived(store, "d2", "D2", "d1 * 2")
    with pytest.raises(DerivationConflict, match="already exists"):
        derive_service.create_derived(store, "d1", "D1", "a * 3")
    with pytest.raises(DerivationConflict, match="cycle"):
        derive_service.create_derived(store, "d1", "D1", "d2 * 3", overwrite=True)
    derive_service.create_derived(store, "d1", "D1b", "a * 3", overwrite=True)
    assert store.get_observations("d1")["y"].iloc[0] == a["y"].iloc[0] * 3
    # change a source, then recompute the derived series
    a2 = a.copy()
    a2["y"] = a2["y"] + 1
    store.upsert_series(meta("a"), a2)
    created = store.get_meta("d2").created_at
    derive_service.recompute(store, "d2")
    m = store.get_meta("d2")
    assert m.created_at == created and "recomputed_at" in m.lineage
    with pytest.raises(DerivationConflict, match="not a derived"):
        derive_service.recompute(store, "a")


def test_lineage_fingerprint_changes_with_source_data(store):
    store.upsert_series(meta("a"), count_series(2, seed=1))
    d1 = derive_service.plan(store, "x", "x", "a * 2").meta.lineage["sources"][0]["sha256_16"]
    store.upsert_series(meta("a"), count_series(2, seed=2))
    d2 = derive_service.plan(store, "x", "x", "a * 2").meta.lineage["sources"][0]["sha256_16"]
    assert d1 != d2 and len(d1) == 16


def test_event_kind_and_rate_chain(store):
    store.upsert_series(meta("ev", "event"), count_series(2, level=0.5, amplitude=0))
    d = derive_service.plan(store, "x", "x", "ev + ev")
    assert d.meta.indicator_type == "event"
    r = derive_service.plan(store, "y", "y", "diff(ev)")
    assert r.meta.indicator_type == "rate" and isinstance(r.data, pd.DataFrame)
