"""CDC FluView (ILINet) national weekly series via the Delphi Epidata API.

Network fetch is optional: a small cached fixture ships with the package so that tests, the demo
and the Docker image work offline. Missing values stay missing (never filled with 0).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from importlib import resources

import httpx
import pandas as pd

DELPHI_URL = "https://api.delphi.cmu.edu/epidata/fluview/"
FIELDS = "epiweek,issue,lag,num_ili,num_patients,wili,ili"
METRICS = {
    "wili": ("Weighted ILI (% of outpatient visits)", "proportion", "percent"),
    "num_ili": ("ILI outpatient visits", "count", "visits"),
}


def epiweek_to_date(epiweek: int) -> dt.date:
    """Start date (Sunday) of an MMWR epiweek given as YYYYWW."""
    year, week = divmod(int(epiweek), 100)
    jan1 = dt.date(year, 1, 1)
    sunday_based = (jan1.weekday() + 1) % 7  # Sunday = 0
    week1 = (
        jan1 - dt.timedelta(days=sunday_based)
        if sunday_based <= 3
        else jan1 + dt.timedelta(days=7 - sunday_based)
    )
    return week1 + dt.timedelta(weeks=week - 1)


def date_to_epiweek(d: dt.date) -> int:
    """Inverse of :func:`epiweek_to_date` for a Sunday start date."""
    year = d.year + 1 if epiweek_to_date((d.year + 1) * 100 + 1) <= d else d.year
    if epiweek_to_date(year * 100 + 1) > d:
        year -= 1
    week = (d - epiweek_to_date(year * 100 + 1)).days // 7 + 1
    return year * 100 + week


def fetch_fluview(
    region: str = "nat",
    start_epiweek: int = 201040,
    end_epiweek: int | None = None,
    timeout: float = 60.0,
    api_key: str | None = None,
) -> pd.DataFrame:
    """Fetch the latest issue of every epiweek in range from Delphi (network)."""
    if end_epiweek is None:
        today = dt.date.today()
        end_epiweek = date_to_epiweek(today - dt.timedelta(days=(today.weekday() + 1) % 7))
    params = {
        "regions": region,
        "epiweeks": f"{start_epiweek}-{end_epiweek}",
        "fields": FIELDS,
    }
    if api_key:
        params["api_key"] = api_key
    r = httpx.get(DELPHI_URL, params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    if payload.get("result") != 1:
        raise RuntimeError(f"Delphi returned: {payload.get('message', payload)}")
    df = pd.DataFrame(payload["epidata"])
    return df.sort_values("epiweek").reset_index(drop=True)


def fixture_dataframe() -> pd.DataFrame:
    with resources.files("ailab.data").joinpath("fluview_nat_fixture.csv").open("rb") as fh:
        return pd.read_csv(fh)


def fixture_meta() -> dict:
    return json.loads(
        resources.files("ailab.data").joinpath("fluview_nat_fixture.meta.json").read_text("utf-8")
    )


def to_series(df: pd.DataFrame, metric: str = "wili") -> pd.DataFrame:
    """Raw Delphi rows -> ``ds, y`` weekly frame (ds = Sunday week start)."""
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {sorted(METRICS)}")
    out = pd.DataFrame(
        {
            "ds": pd.to_datetime([epiweek_to_date(int(e)) for e in df["epiweek"]]),
            "y": pd.to_numeric(df[metric], errors="coerce"),
        }
    )
    return out.sort_values("ds").reset_index(drop=True)


def dataframe_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()
