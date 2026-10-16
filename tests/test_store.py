import numpy as np
import pandas as pd
import pytest

from ailab.schema import AlertRule, SeriesMeta
from ailab.store import DependencyError, SeriesStore

from .conftest import count_series, meta


def test_roundtrip_keeps_nulls_parquet_and_persistence(tmp_path):
    st = SeriesStore(tmp_path / "x.duckdb")
    df = count_series(2)
    df.loc[3, "y"] = np.nan
    assert st.upsert_series(meta(), df) == len(df)
    out = st.get_observations("t_series")
    assert out["y"].isna().sum() == 1  # not zero-filled
    p = st.export_parquet("t_series", tmp_path / "t.parquet")
    st.import_parquet(meta("t_copy"), p)
    assert len(st.get_observations("t_copy")) == len(df)
    st.close()
    st2 = SeriesStore(tmp_path / "x.duckdb")  # persistence across reopen
    assert [m.series_id for m in st2.list_series()] == ["t_copy", "t_series"]
    assert st2.get_meta("t_series").created_at


def test_rules_and_delete(store):
    store.upsert_series(meta(), count_series(2))
    store.upsert_rule(AlertRule(rule_id="r1", series_id="t_series", method="cusum"))
    assert len(store.list_rules("t_series")) == 1
    store.delete_series("t_series")
    assert store.list_rules() == [] and store.count_series() == 0
    with pytest.raises(KeyError):
        store.get_meta("t_series")


def test_invalid_input_rejected(store):
    with pytest.raises(ValueError):
        SeriesMeta(series_id="Bad-ID", name="x")
    with pytest.raises(ValueError):
        SeriesMeta(series_id="ok", name="  ")
    dup = pd.DataFrame({"ds": ["2024-01-07"] * 2, "y": [1, 2]})
    with pytest.raises(ValueError, match="duplicate"):
        store.upsert_series(meta(), dup)
    off_grid = pd.DataFrame({"ds": ["2024-01-07", "2024-01-15"], "y": [1, 2]})
    with pytest.raises(ValueError, match="weekly grid"):
        store.upsert_series(meta(), off_grid)
    with pytest.raises(ValueError, match="numeric"):
        store.upsert_series(meta(), pd.DataFrame({"ds": ["2024-01-07"], "y": ["abc"]}))
    with pytest.raises(ValueError, match="finite"):
        store.upsert_series(meta(), pd.DataFrame({"ds": ["2024-01-07"], "y": [np.inf]}))
    with pytest.raises(ValueError, match="invalid date"):
        store.upsert_series(meta(), pd.DataFrame({"ds": ["not-a-date"], "y": [1]}))


def test_values_validated_against_kind(store):
    ds = pd.date_range("2024-01-07", periods=3, freq="7D")
    with pytest.raises(ValueError, match="non-negative"):
        store.upsert_series(meta(), pd.DataFrame({"ds": ds, "y": [1, -1, 2]}))
    with pytest.raises(ValueError, match="proportion"):
        store.upsert_series(meta(itype="proportion"), pd.DataFrame({"ds": ds, "y": [0.1, 1.2, 0]}))
    store.upsert_series(
        meta(itype="proportion", value_scale=100), pd.DataFrame({"ds": ds, "y": [10, 100, 0]})
    )
    with pytest.raises(ValueError, match="proportion"):
        store.upsert_series(
            meta("p2", "proportion", value_scale=100), pd.DataFrame({"ds": ds, "y": [101, 1, 2]})
        )
    # rates may be anything finite (e.g. differences)
    store.upsert_series(meta("r1", "rate"), pd.DataFrame({"ds": ds, "y": [-1.5, 2, 3]}))


def test_pagination_filters_and_observation_paging(store):
    for i in range(5):
        store.upsert_series(
            meta(f"s{i}", "count" if i % 2 == 0 else "rate"), count_series(1, seed=i)
        )
    page, total = store.page_series(2, 1)
    assert total == 5 and [m.series_id for m in page] == ["s1", "s2"]
    assert store.page_series(10, 0, "rate")[1] == 2
    assert store.page_series(10, 0, derived=True)[1] == 0
    obs = store.get_observations("s0", limit=4, offset=2)
    assert len(obs) == 4 and store.count_observations("s0") == 13
    assert obs["ds"].iloc[0] == store.get_observations("s0")["ds"].iloc[2]


def test_delete_refuses_when_dependents_exist(store):
    from ailab import derive_service

    store.upsert_series(meta("a"), count_series(3, seed=1))
    store.upsert_series(meta("b"), count_series(3, seed=2))
    derive_service.create_derived(store, "ab", "A+B", "a + b")
    derive_service.create_derived(store, "ab2", "x2", "ab * 2")
    assert store.dependents("a") == ["ab", "ab2"]
    with pytest.raises(DependencyError) as e:
        store.delete_series("a")
    assert e.value.dependents == ["ab", "ab2"]
    assert store.delete_series("a", cascade=True) == ["ab", "ab2", "a"]
    assert [m.series_id for m in store.list_series()] == ["b"]
    assert store.dependents("b") == []
