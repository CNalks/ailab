import numpy as np
import pandas as pd
import pytest

from ailab import derive
from ailab.schema import AlertRule, SeriesMeta
from ailab.store import SeriesStore

from .conftest import make_weekly, meta


def test_store_roundtrip_keeps_nulls_and_parquet(tmp_path):
    st = SeriesStore(tmp_path / "x.duckdb")
    df = make_weekly(20)
    df.loc[3, "y"] = np.nan
    assert st.upsert_series(meta(), df) == 20
    out = st.get_observations("t_series")
    assert out["y"].isna().sum() == 1  # not zero-filled
    p = st.export_parquet("t_series", tmp_path / "t.parquet")
    st.import_parquet(meta("t_copy"), p)
    assert len(st.get_observations("t_copy")) == 20
    st.close()
    # persistence across reopen
    st2 = SeriesStore(tmp_path / "x.duckdb")
    assert [m.series_id for m in st2.list_series()] == ["t_copy", "t_series"]


def test_rules_and_delete():
    st = SeriesStore()
    st.upsert_series(meta(), make_weekly(30))
    st.upsert_rule(AlertRule(rule_id="r1", series_id="t_series", method="cusum"))
    assert len(st.list_rules("t_series")) == 1
    st.delete_series("t_series")
    assert st.list_rules() == [] and st.count_series() == 0
    with pytest.raises(KeyError):
        st.get_meta("t_series")


def test_invalid_ids_rejected():
    with pytest.raises(ValueError):
        SeriesMeta(series_id="Bad-ID", name="x")
    with pytest.raises(ValueError):
        SeriesStore().upsert_series(meta(), pd.DataFrame({"ds": ["2024-01-07"] * 2, "y": [1, 2]}))


def test_derive_arithmetic_and_functions():
    idx = pd.date_range("2024-01-07", periods=6, freq="7D")
    a = pd.Series([1, 2, 3, 4, 5, 6.0], index=idx)
    b = pd.Series([2, 0, 2, 2, np.nan, 2.0], index=idx)
    r = derive.evaluate("a / b * 100", {"a": a, "b": b})
    assert r.iloc[0] == 50 and np.isnan(r.iloc[1]) and np.isnan(r.iloc[4])  # /0 and NaN -> missing
    assert derive.evaluate("ma(a, 3)", {"a": a}).iloc[2] == 2.0
    assert derive.evaluate("diff(a, 1)", {"a": a}).iloc[1] == 1.0
    assert derive.referenced_ids("ma(a, 3) + b / 2") == ["a", "b"]


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')",
        "a.__class__",
        "open('/etc/passwd')",
        "a ** 9999",
        "[x for x in a]",
        "ma(a, 100000)",
        "a if a else a",
        "lambda: 1",
        "'x' * 3",
        "ma(a, n=3)",
        "1 + 2",
    ],
)
def test_derive_rejects_unsafe_or_invalid(expr):
    a = pd.Series([1.0, 2.0], index=pd.date_range("2024-01-07", periods=2, freq="7D"))
    with pytest.raises(derive.ExpressionError):
        derive.evaluate(expr, {"a": a})
