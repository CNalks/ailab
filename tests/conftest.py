import numpy as np
import pandas as pd
import pytest

from ailab.schema import SeriesMeta


def make_weekly(n_weeks: int, kind: str = "seasonal", seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ds = pd.date_range("2012-01-01", periods=n_weeks, freq="7D")  # Sundays
    t = np.arange(n_weeks)
    if kind == "seasonal":
        y = 50 + 40 * np.maximum(0, np.sin(2 * np.pi * t / 52)) ** 2 + rng.normal(0, 2, n_weeks)
    elif kind == "noise":
        y = 50 + rng.normal(0, 10, n_weeks)  # nothing to learn beyond the mean
    elif kind == "sparse":
        y = (rng.random(n_weeks) < 0.15).astype(float) * rng.integers(1, 4, n_weeks)
    else:
        raise ValueError(kind)
    return pd.DataFrame({"ds": ds, "y": np.round(np.clip(y, 0, None), 3)})


def meta(sid: str = "t_series", itype: str = "count") -> SeriesMeta:
    return SeriesMeta(series_id=sid, name="test", indicator_type=itype)


@pytest.fixture
def seasonal():
    return make_weekly(52 * 8, "seasonal")
