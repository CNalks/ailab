"""Precompute the data of the public showcase site (site/public/data) with the real ailab pipeline.

Inputs are committed snapshots (site/data-src, with provenance) plus the bundled CDC FluView fixture, so the
result is reproducible offline:  uv run python scripts/build_showcase_data.py
Output: manifest.json, sources.json, series/<id>.json, downloads/<id>.parquet (see docs/EXPORT_FORMAT.md).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

from ailab.demo import load_fluview_demo
from ailab.export import export_site
from ailab.schema import SeriesMeta
from ailab.sources import get_source
from ailab.store import SeriesStore

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "site" / "data-src"
OUT = ROOT / "site" / "public" / "data"
PROV = json.loads((SRC / "provenance.json").read_text("utf-8"))


def _meta(sid: str, name: str, file: str, **kw) -> SeriesMeta:
    p = PROV[file]
    src = get_source(p["source_id"])
    return SeriesMeta(
        series_id=sid,
        name=name,
        source_id=src.source_id,
        licence=src.licence,
        attribution=src.attribution,
        notice=src.notice,
        retrieved_at=p["retrieved_at"],
        sha256=p.get("upstream_sha256", ""),
        **kw,
    )


def _erviss(file: str) -> pd.DataFrame:
    x = pd.read_csv(SRC / file)
    ds = [
        pd.Timestamp(dt.date.fromisocalendar(int(w[:4]), int(w[6:]), 1))  # ISO week -> Monday
        for w in x["yearweek"]
    ]
    d = pd.DataFrame({"ds": ds, "y": x["value"].to_numpy()}).sort_values("ds")
    grid = pd.DataFrame({"ds": pd.date_range(d["ds"].min(), d["ds"].max(), freq="7D")})
    return grid.merge(d, how="left", on="ds")  # absent weeks stay NULL, never 0


def main() -> int:
    store = SeriesStore()
    load_fluview_demo(store)  # bundled fixture: US national wILI and ILI visits
    uk = pd.read_csv(SRC / "ukhsa_england_influenza_positivity.csv")
    store.upsert_series(
        _meta(
            "england_flu_positivity",
            "England: influenza test positivity (UKHSA)",
            "ukhsa_england_influenza_positivity.csv",
            disease="Influenza",
            geography="England",
            unit="% of tests positive",
            indicator_type="proportion",
            value_scale=100,
        ),
        pd.DataFrame({"ds": pd.to_datetime(uk["date"]), "y": uk["metric_value"]}),
    )
    store.upsert_series(
        _meta(
            "eu_eea_flu_positivity",
            "EU/EEA: influenza positivity, primary-care sentinel (ERVISS)",
            "erviss_eu_eea_influenza_positivity.csv",
            disease="Influenza",
            geography="EU/EEA (aggregate)",
            unit="% of sentinel specimens positive",
            indicator_type="proportion",
            value_scale=100,
        ),
        _erviss("erviss_eu_eea_influenza_positivity.csv"),
    )
    store.upsert_series(
        _meta(
            "malta_flu_detections",
            "Malta: influenza detections, primary-care sentinel (ERVISS)",
            "erviss_malta_influenza_detections.csv",
            disease="Influenza",
            geography="Malta",
            unit="detections per week",
            indicator_type="count",
        ),
        _erviss("erviss_malta_influenza_detections.csv"),
    )
    if OUT.exists():
        shutil.rmtree(OUT)
    ids = [
        "us_ilinet_wili",
        "us_ilinet_ili_visits",
        "england_flu_positivity",
        "eu_eea_flu_positivity",
        "malta_flu_detections",
    ]
    m = export_site(store, OUT, series_ids=ids, horizon=8)
    for s in m["series"]:
        print(
            f"{s['series_id']:24s} gate={s['gate']['decision']:8s} {s['gate']['reason_codes']} "
            f"through={s['data_through']} gz={s['gzip_bytes']}B"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
