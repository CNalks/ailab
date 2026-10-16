"""Static export: manifest.json + series/<id>.json + downloads/<id>.parquet + sources.json.

Format documented in docs/EXPORT_FORMAT.md. Budgets: each series JSON <= 60 KB gzipped, whole
export <= 1.5 MB of JSON (parquet downloads excluded). Exceeding a budget raises BudgetError.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path

from . import __version__
from .pipeline import SCHEMA_VERSION, analyse
from .sources import get_source
from .store import SeriesStore

MAX_SERIES_GZ = 60 * 1024
MAX_TOTAL_JSON = 1_500_000


class BudgetError(RuntimeError):
    pass


def export_site(
    store: SeriesStore,
    out: str | Path,
    series_ids: list[str] | None = None,
    horizon: int = 8,
    generated_at: str | None = None,
    parquet: bool = True,
) -> dict:
    out = Path(out)
    (out / "series").mkdir(parents=True, exist_ok=True)
    ids = series_ids or [m.series_id for m in store.list_series()]
    entries = []
    sources_used: dict[str, dict] = {}
    total = 0
    for sid in ids:
        res = analyse(store, sid, horizon=horizon)
        blob = json.dumps(res, ensure_ascii=False, separators=(",", ":")).encode()
        gz = len(gzip.compress(blob, 9, mtime=0))
        if gz > MAX_SERIES_GZ:
            raise BudgetError(f"{sid}: {gz} B gzipped exceeds {MAX_SERIES_GZ}")
        (out / "series" / f"{sid}.json").write_bytes(blob)
        total += len(blob)
        entry = {
            "series_id": sid,
            "name": res["series"]["name"],
            "path": f"series/{sid}.json",
            "bytes": len(blob),
            "gzip_bytes": gz,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "data_through": res["data_through"],
            "indicator_type": res["series"]["indicator_type"],
            "derived_from": res["series"]["derived_from"],
            "source_id": res["series"]["source_id"],
            "policy": {
                k: res["policy"][k]
                for k in (
                    "forecast_allowed",
                    "reason",
                    "display_mode",
                    "model_family",
                    "horizon_weeks",
                )
            },
            "quality_grade": res["quality"]["grade"],
            "gate": {
                "decision": res["gate"]["decision"],
                "confidence": res["gate"]["confidence"],
                "reason_codes": res["gate"]["reason_codes"],
            },
            "latest_alert_signal": {a["method"]: a["latest_is_signal"] for a in res["alerts"]},
        }
        if parquet:
            pq = store.export_parquet(sid, out / "downloads" / f"{sid}.parquet")
            entry["download"] = f"downloads/{pq.name}"
        entries.append(entry)
        # sources of this series and (for derived series) of everything it was computed from
        sids = {res["series"]["source_id"]}
        for src in res["series"]["derived_from"]:
            sids.add(store.get_meta(src).source_id)
        for sid_ in sorted(x for x in sids if x):
            try:
                sources_used[sid_] = get_source(sid_).to_dict()
            except KeyError:
                pass
    if total > MAX_TOTAL_JSON:
        raise BudgetError(f"total JSON {total} B exceeds {MAX_TOTAL_JSON}")
    (out / "sources.json").write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "sources": list(sources_used.values())},
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "ailab_version": __version__,
        "generated_at": generated_at or dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "static_precomputed": True,
        "disclaimer": "Precomputed statistical analysis of public data; not official, "
        "not public-health advice.",
        "series": entries,
        "sources": "sources.json",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    return manifest
