"""Demo datasets: load the bundled (or freshly fetched) CDC FluView national series."""

from __future__ import annotations

import pandas as pd

from .schema import SeriesMeta
from .sources import fluview, get_source
from .store import SeriesStore

DEMO_SERIES = {
    "us_ilinet_wili": "wili",
    "us_ilinet_ili_visits": "num_ili",
}


def load_fluview_demo(store: SeriesStore, refresh: bool = False) -> dict:
    """Populate ``store`` with the FluView demo series. Returns provenance info."""
    src = get_source("cdc_fluview")
    stale = False
    note = ""
    if refresh:
        try:
            raw = fluview.fetch_fluview()
            meta_info = {
                "retrieved_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(timespec="seconds"),
                "sha256": fluview.dataframe_sha256(raw),
            }
        except Exception as exc:  # network failure: fall back to the fixture and say so
            raw = fluview.fixture_dataframe()
            meta_info = fluview.fixture_meta()
            stale = True
            note = f"refresh failed ({type(exc).__name__}); used bundled fixture"
    else:
        raw = fluview.fixture_dataframe()
        meta_info = fluview.fixture_meta()
    for sid, metric in DEMO_SERIES.items():
        title, itype, unit = fluview.METRICS[metric]
        meta = SeriesMeta(
            series_id=sid,
            name=f"US national — {title}",
            disease="Influenza-like illness",
            geography="United States (national)",
            unit=unit,
            indicator_type=itype,
            value_scale=100 if itype == "proportion" else 1,
            source_id=src.source_id,
            licence=src.licence,
            attribution=src.attribution,
            notice=src.notice,
            retrieved_at=meta_info["retrieved_at"],
            sha256=meta_info["sha256"],
        )
        store.upsert_series(meta, fluview.to_series(raw, metric))
    return {"series": list(DEMO_SERIES), "stale": stale, "note": note, **meta_info}
