"""Shared fixtures. Unit tests use the seeded synthetic generator (ailab.synth) with a short
season (13 weeks) so backtests are fast; ONE real CDC FluView fixture is used only by
tests/test_e2e_fluview.py."""

import numpy as np
import pandas as pd
import pytest

from ailab.schema import SeriesMeta
from ailab.store import SeriesStore
from ailab.synth import synth_series

SL = 13  # season length used by the fast synthetic tests


def meta(sid: str = "t_series", itype: str = "count", season_length: int = SL, **kw) -> SeriesMeta:
    return SeriesMeta(
        series_id=sid, name="test", indicator_type=itype, season_length=season_length, **kw
    )


def count_series(n_seasons: int = 8, seed: int = 0, **kw) -> pd.DataFrame:
    return synth_series(SL * n_seasons, "count", seed, season_length=SL, **kw)


def prop_series(n_seasons: int = 8, seed: int = 0, **kw) -> pd.DataFrame:
    return synth_series(SL * n_seasons, "proportion", seed, season_length=SL, **kw)


def flat_series(n: int = 200, seed: int = 0, level: float = 100.0) -> pd.DataFrame:
    """No seasonality: pure Poisson noise around ``level``."""
    return synth_series(
        n, "count", seed, season_length=SL, level=level, amplitude=0.0, dispersion=0
    )


def sparse_series(n: int = 52 * 4, seed: int = 0) -> pd.DataFrame:
    return synth_series(n, "count", seed, season_length=SL, level=0.08, amplitude=0.0, dispersion=0)


@pytest.fixture
def seasonal() -> pd.DataFrame:
    return count_series(8)


@pytest.fixture
def store() -> SeriesStore:
    s = SeriesStore()
    yield s
    s.close()


@pytest.fixture
def idx():
    return pd.date_range("2024-01-07", periods=6, freq="7D")


def ser(values, idx) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float), index=idx)
