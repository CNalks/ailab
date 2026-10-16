import numpy as np
import pandas as pd
import pytest

from ailab import derive
from ailab.derive import AlignmentError


def frame(start, n, vals=None, skip=()):
    ds = pd.date_range(start, periods=n, freq="7D")
    y = np.arange(1, n + 1, dtype=float) if vals is None else np.asarray(vals, dtype=float)
    df = pd.DataFrame({"ds": ds, "y": y})
    return df.drop(index=list(skip)).reset_index(drop=True)


def test_intersection_default_trims_to_overlap():
    out, rep = derive.align({"a": frame("2024-01-07", 10), "b": frame("2024-02-04", 10)})
    assert rep["mode"] == "intersection" and rep["n_periods"] == 6
    assert rep["grid_start"] == "2024-02-04" and rep["grid_end"] == "2024-03-10"
    assert out["a"].index.equals(out["b"].index) and out["a"].notna().all()
    assert rep["per_source"]["a"]["n_outside_grid"] == 4


def test_union_pads_with_missing_never_zero():
    out, rep = derive.align(
        {"a": frame("2024-01-07", 4), "b": frame("2024-01-21", 4)}, mode="union"
    )
    assert rep["n_periods"] == 6
    assert np.isnan(out["a"].iloc[-2:]).all() and np.isnan(out["b"].iloc[:2]).all()
    assert (out["a"].dropna() > 0).all()


def test_gaps_inside_range_are_missing_and_reported():
    out, rep = derive.align({"a": frame("2024-01-07", 8, skip=[3]), "b": frame("2024-01-07", 8)})
    assert np.isnan(out["a"].iloc[3]) and rep["per_source"]["a"]["n_missing_in_grid"] == 1
    assert rep["per_source"]["b"]["n_missing_in_grid"] == 0


def test_nan_values_counted_as_missing():
    a = frame("2024-01-07", 5, vals=[1, np.nan, 3, 4, 5])
    out, rep = derive.align({"a": a})
    assert rep["per_source"]["a"]["n_missing_in_grid"] == 1


def test_different_weekday_phase_is_an_error():
    sunday = frame("2024-01-07", 8)
    monday = frame("2024-01-08", 8)
    with pytest.raises(AlignmentError, match="same weekly grid"):
        derive.align({"a": sunday, "b": monday})


def test_no_overlap_and_empty_and_bad_mode():
    with pytest.raises(AlignmentError, match="overlap"):
        derive.align({"a": frame("2020-01-05", 4), "b": frame("2024-01-07", 4)})
    # union has no such problem
    out, rep = derive.align({"a": frame("2020-01-05", 2), "b": frame("2020-02-02", 2)}, "union")
    assert rep["n_periods"] == 6
    with pytest.raises(AlignmentError):
        derive.align({"a": frame("2024-01-07", 0)})
    with pytest.raises(AlignmentError):
        derive.align({"a": frame("2024-01-07", 3)}, mode="outer")
    with pytest.raises(AlignmentError):
        derive.align({})
