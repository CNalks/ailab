import datetime as dt

import pandas as pd

from ailab.sources import fluview


def test_epiweek_roundtrip_and_known_dates():
    assert fluview.epiweek_to_date(202639) == dt.date(2026, 9, 27)
    assert fluview.epiweek_to_date(202101) == dt.date(2021, 1, 3)
    assert fluview.epiweek_to_date(202053) == dt.date(2020, 12, 27)  # 2020 has 53 MMWR weeks
    for ew in (201040, 202053, 202101, 202639):
        assert fluview.date_to_epiweek(fluview.epiweek_to_date(ew)) == ew


def test_fixture_is_complete_weekly_grid_without_zero_fill():
    df = fluview.fixture_dataframe()
    s = fluview.to_series(df, "wili")
    assert s["y"].notna().all() and (s["y"] > 0).all()
    assert (s["ds"].diff().dropna() == pd.Timedelta(days=7)).all()
    assert len(s) == fluview.fixture_meta()["rows"]


def test_to_series_keeps_missing_as_nan():
    df = pd.DataFrame({"epiweek": [202501, 202502], "wili": [1.0, None]})
    s = fluview.to_series(df, "wili")
    assert s["y"].isna().tolist() == [False, True]
