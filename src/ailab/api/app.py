"""Thin service API. Embedded DuckDB, in-process work, no Postgres/Redis/Celery/Supabase.

Auth model: read endpoints are open. Write endpoints (create/delete series, derive, alert rules,
export) require the ``X-API-Key`` header to equal env ``AILAB_API_KEY``. If that variable is not
set the service runs read-only (writes return 403). There is no default key.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .. import __version__, alerts, derive, quality
from ..demo import load_fluview_demo
from ..export import export_site
from ..pipeline import analyse
from ..schema import AlertRule, SeriesMeta
from ..store import SeriesStore


class ObservationIn(BaseModel):
    ds: str = Field(description="Week start date, YYYY-MM-DD (Sunday)")
    y: float | None = Field(default=None, description="null = missing (never zero-filled)")


class SeriesIn(BaseModel):
    meta: SeriesMeta
    observations: list[ObservationIn]


class DeriveIn(BaseModel):
    series_id: str
    name: str
    expr: str = Field(description="e.g. 'ma(us_ilinet_wili, 4)' or 'us_ilinet_ili_visits / 1000'")
    indicator_type: str = "rate"


def create_app(
    db_path: str | Path | None = None,
    preload_demo: bool = False,
    api_key: str | None = None,
    out_dir: str | Path | None = None,
) -> FastAPI:
    store = SeriesStore(db_path or os.environ.get("AILAB_DB") or ":memory:")
    key = api_key if api_key is not None else os.environ.get("AILAB_API_KEY") or None
    out = Path(out_dir or os.environ.get("AILAB_OUT", "out"))
    if preload_demo and store.count_series() == 0:
        load_fluview_demo(store, refresh=False)
    started = time.time()
    compute_lock = threading.Lock()  # CPU-bound analysis is serialised: predictable memory use
    cache: dict[tuple, dict] = {}

    app = FastAPI(
        title="ailab service API",
        version=__version__,
        description="Time-series engine for infectious-disease surveillance. Forecasts carry a "
        "policy (forecast_allowed / reason / display_mode / model_family / horizon_weeks / "
        "indicator_type). Statistical analysis of public data; not public-health advice.",
    )
    app.state.store = store

    def require_write(x_api_key: str | None = Header(default=None)) -> None:
        if key is None:
            raise HTTPException(403, "service is read-only (AILAB_API_KEY not configured)")
        if x_api_key is None or not secrets.compare_digest(x_api_key, key):
            raise HTTPException(401, "invalid or missing X-API-Key")

    def _meta(series_id: str) -> SeriesMeta:
        try:
            return store.get_meta(series_id)
        except KeyError:
            raise HTTPException(404, f"unknown series {series_id!r}") from None

    @app.get("/health", tags=["system"])
    def health() -> dict:
        """Liveness + light readiness check (does not run models)."""
        try:
            n = store.count_series()
            db_ok = True
        except Exception:
            n, db_ok = -1, False
        return {
            "status": "ok" if db_ok else "degraded",
            "version": __version__,
            "uptime_s": round(time.time() - started, 1),
            "store": {"engine": "duckdb", "ok": db_ok, "series": n},
            "writable": key is not None,
        }

    # ---- series ------------------------------------------------------------------------
    @app.get("/api/v1/series", tags=["series"])
    def list_series() -> list[SeriesMeta]:
        return store.list_series()

    @app.post(
        "/api/v1/series", tags=["series"], dependencies=[Depends(require_write)], status_code=201
    )
    def create_series(body: SeriesIn) -> dict:
        df = pd.DataFrame([o.model_dump() for o in body.observations])
        if df.empty:
            raise HTTPException(422, "observations must not be empty")
        try:
            n = store.upsert_series(body.meta, df)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        cache.clear()
        return {"series_id": body.meta.series_id, "observations": n}

    @app.get("/api/v1/series/{series_id}", tags=["series"])
    def get_series(series_id: str) -> SeriesMeta:
        return _meta(series_id)

    @app.delete(
        "/api/v1/series/{series_id}", tags=["series"], dependencies=[Depends(require_write)]
    )
    def delete_series(series_id: str) -> dict:
        _meta(series_id)
        store.delete_series(series_id)
        cache.clear()
        return {"deleted": series_id}

    @app.get("/api/v1/series/{series_id}/observations", tags=["series"])
    def observations(series_id: str, start: str | None = None, end: str | None = None) -> dict:
        _meta(series_id)
        df = store.get_observations(series_id, start, end)
        return {
            "series_id": series_id,
            "observations": [
                {"ds": d.date().isoformat(), "y": None if pd.isna(v) else float(v)}
                for d, v in zip(df["ds"], df["y"], strict=True)
            ],
        }

    @app.get("/api/v1/series/{series_id}/quality", tags=["series"])
    def series_quality(series_id: str) -> dict:
        return quality.check_series(_meta(series_id), store.get_observations(series_id))

    # ---- derived -----------------------------------------------------------------------
    @app.post(
        "/api/v1/derive", tags=["derive"], dependencies=[Depends(require_write)], status_code=201
    )
    def derive_series(body: DeriveIn) -> dict:
        """Create a derived series from an expression over stored series (AST whitelist)."""
        try:
            ids = derive.referenced_ids(body.expr)
            if not ids:
                raise derive.ExpressionError("expression must reference at least one series")
            frames = {i: store.get_observations(i).set_index("ds")["y"] for i in ids}
            res = derive.evaluate(body.expr, frames)
            base = _meta(ids[0])
            meta = SeriesMeta(
                series_id=body.series_id,
                name=body.name,
                disease=base.disease,
                geography=base.geography,
                indicator_type=body.indicator_type,
                season_length=base.season_length,
                source_id=base.source_id,
                licence=base.licence,
                attribution=base.attribution,
                notice=base.notice,
                derived_expr=body.expr,
                derived_from=ids,
            )
            n = store.upsert_series(
                meta, res.dropna().rename("y").reset_index().rename(columns={"index": "ds"})
            )
        except (derive.ExpressionError, KeyError, ValueError) as exc:
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from None
        cache.clear()
        return {"series_id": body.series_id, "observations": n, "derived_from": ids}

    # ---- forecast ----------------------------------------------------------------------
    @app.get("/api/v1/series/{series_id}/forecast", tags=["forecast"])
    def forecast(series_id: str, horizon: int = Query(8, ge=1, le=26)) -> dict:
        """Backtest + policy + forecast. ``policy.forecast_allowed=false`` => no forecast values."""
        meta = _meta(series_id)
        obs = store.get_observations(series_id)
        ck = (
            series_id,
            horizon,
            hashlib.sha1(
                pd.util.hash_pandas_object(obs, index=False).to_numpy().tobytes()
            ).hexdigest(),
            meta.model_dump_json(),
        )
        if ck not in cache:
            with compute_lock:
                if ck not in cache:
                    cache[ck] = analyse(
                        store, series_id, horizon=horizon, include_observations=False
                    )
        res = cache[ck]
        return {
            k: res[k]
            for k in (
                "series",
                "data_through",
                "quality",
                "policy",
                "backtest",
                "forecast",
                "schema_version",
            )
        }

    @app.get("/api/v1/series/{series_id}/policy", tags=["forecast"])
    def policy(series_id: str, horizon: int = Query(8, ge=1, le=26)) -> dict:
        return forecast(series_id, horizon)["policy"]

    # ---- alerts ------------------------------------------------------------------------
    @app.get("/api/v1/alerts/rules", tags=["alerts"])
    def list_rules(series_id: str | None = None) -> list[AlertRule]:
        return store.list_rules(series_id)

    @app.put("/api/v1/alerts/rules", tags=["alerts"], dependencies=[Depends(require_write)])
    def put_rule(rule: AlertRule) -> AlertRule:
        _meta(rule.series_id)
        store.upsert_rule(rule)
        return rule

    @app.delete(
        "/api/v1/alerts/rules/{rule_id}", tags=["alerts"], dependencies=[Depends(require_write)]
    )
    def delete_rule(rule_id: str) -> dict:
        if not store.delete_rule(rule_id):
            raise HTTPException(404, "unknown rule")
        return {"deleted": rule_id}

    @app.get("/api/v1/series/{series_id}/alerts", tags=["alerts"])
    def series_alerts(
        series_id: str,
        method: str = Query("ears_c2", pattern="^(ears_c1|ears_c2|ears_c3|cusum)$"),
        baseline: int = Query(7, ge=3, le=104),
    ) -> dict:
        """Ad-hoc statistical-signal scan (no stored rule needed)."""
        _meta(series_id)
        g, _ = quality.model_input(store.get_observations(series_id))
        return {
            "series_id": series_id,
            "method": method,
            **alerts.summarise(alerts.detect(g, method, baseline)),
        }

    @app.get("/api/v1/alerts/evaluate", tags=["alerts"])
    def evaluate_rules() -> list[dict]:
        """Evaluate all stored, enabled alert rules on their series."""
        out = []
        for rule in store.list_rules():
            if not rule.enabled:
                continue
            g, _ = quality.model_input(store.get_observations(rule.series_id))
            out.append(
                {"rule": rule.model_dump(), **alerts.summarise(alerts.evaluate_rule(rule, g))}
            )
        return out

    # ---- export ------------------------------------------------------------------------
    @app.post("/api/v1/export", tags=["export"], dependencies=[Depends(require_write)])
    def export() -> dict:
        """Write the static export (manifest + series JSON + Parquet) to AILAB_OUT."""
        with compute_lock:
            return export_site(store, out)

    return app
