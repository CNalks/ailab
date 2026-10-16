"""Service API (FastAPI). Embedded DuckDB, in-process work, no Postgres/Redis/Celery/Supabase.

Auth model: read endpoints are open. Write endpoints (create/delete/recompute series, derive,
alert rules, static export) require header ``X-API-Key`` equal to env ``AILAB_API_KEY`` (>= 16
chars). If the variable is unset the service is read-only (writes return 403). No default key.
Errors are structured: ``{"error": {"code", "message", "request_id", "details"?}}``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import logging
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__, alerts, derive, derive_service, quality
from ..demo import load_fluview_demo
from ..export import BudgetError, export_site
from ..pipeline import analyse
from ..schema import SERIES_ID_RE, AlertRule, SeriesMeta
from ..store import DependencyError, SeriesStore
from .middleware import (
    BodySizeLimitMiddleware,
    RedactFilter,
    RequestContextMiddleware,
    error_body,
)
from .settings import Settings

logger = logging.getLogger("ailab.api")

SERIES_ID_PATTERN = SERIES_ID_RE.pattern
SeriesId = PathParam(..., pattern=SERIES_ID_PATTERN, max_length=64, description="Series id")
MAX_PAGE = 5000
_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "too_many_requests",
}


# ---- schemas ---------------------------------------------------------------------------
class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list | dict | None = None


class ErrorOut(BaseModel):
    error: ErrorDetail


class ObservationIn(BaseModel):
    ds: dt.date = Field(description="Period start, YYYY-MM-DD; all dates on one weekly grid")
    y: float | None = Field(
        default=None, allow_inf_nan=False, description="null = missing (never zero-filled)"
    )


class SeriesIn(BaseModel):
    meta: SeriesMeta = Field(
        description="Series metadata. `indicator_type` (count|proportion|rate|event) is required."
    )
    observations: list[ObservationIn] = Field(min_length=1, max_length=100_000)


class DeriveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    series_id: str = Field(pattern=SERIES_ID_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    expr: str = Field(
        min_length=1,
        max_length=derive.MAX_EXPR_LEN,
        description="e.g. `positives / tests`, `rolling_mean(A, 7)`, `sum(a, b)`, "
        "`rolling_ratio(pos, tests, 4)`, `lag(A, 52)`. Names are series ids or aliases.",
    )
    bindings: dict[str, str] = Field(
        default_factory=dict,
        max_length=derive.MAX_SOURCES,
        description="Optional alias -> series id (e.g. {'A': 'us_ilinet_num_ili'})",
    )
    align: Literal["intersection", "union"] = "intersection"
    indicator_type: Literal["count", "proportion", "rate", "event"] | None = Field(
        default=None,
        description="Optional assertion; must equal the inferred kind or the request fails.",
    )
    overwrite: bool = False


class Page(BaseModel):
    items: list
    total: int
    limit: int
    offset: int
    next_offset: int | None


ERR = {
    code: {"model": ErrorOut, "description": desc}
    for code, desc in {
        401: "Missing/invalid X-API-Key",
        403: "Service is read-only",
        404: "Unknown series/rule",
        409: "Conflict",
        413: "Request body too large",
        422: "Validation error",
    }.items()
}


def create_app(
    db_path: str | Path | None = None,
    preload_demo: bool | None = None,
    api_key: str | None = None,
    out_dir: str | Path | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env(
        db_path=None if db_path is None else str(db_path),
        out_dir=None if out_dir is None else Path(out_dir),
        api_key=api_key,
        preload_demo=preload_demo,
    )
    key = cfg.api_key
    if key:
        logging.getLogger("ailab").addFilter(RedactFilter([key]))
    store = SeriesStore(cfg.db_path)
    if cfg.preload_demo and store.count_series() == 0:
        load_fluview_demo(store, refresh=False)
    started = time.time()
    write_lock = threading.RLock()  # check-then-write sequences (create/derive/delete) are atomic
    compute_lock = threading.Lock()  # CPU-bound analysis is serialised: predictable memory use
    cache: OrderedDict[tuple, dict] = OrderedDict()
    CACHE_MAX = 64

    app = FastAPI(
        title="ailab service API",
        version=__version__,
        description="Time-series engine for infectious-disease surveillance. Series have an "
        "explicit kind (`indicator_type`: count / proportion / rate / event); forecasts carry a "
        "policy (forecast_allowed / reason / display_mode / model_family / horizon_weeks / "
        "indicator_type). Derived series are created from safe expressions and keep their "
        "lineage. Statistical analysis of public data; not public-health advice.",
        contact={"name": "CNalks", "url": "https://github.com/CNalks"},
        license_info={"name": "Apache-2.0"},
        openapi_tags=[
            {"name": "system", "description": "Liveness/readiness"},
            {"name": "series", "description": "Series CRUD, data, quality, lineage, export"},
            {"name": "derive", "description": "Derived (composite) series"},
            {"name": "forecast", "description": "Backtested forecasts with policy"},
            {"name": "alerts", "description": "Statistical alert signals and rules"},
            {"name": "export", "description": "Static export"},
        ],
    )
    app.state.store = store
    app.state.settings = cfg

    # ---- middleware (outermost last added) --------------------------------------------
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.max_body_bytes)
    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["X-API-Key", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )
    app.add_middleware(RequestContextMiddleware)

    # ---- structured errors --------------------------------------------------------------
    def _err(request: Request, status: int, code: str, message: str, details=None):
        rid = request.scope.get("state", {}).get("request_id")
        return Response(
            error_body(code, message, rid, details),
            status_code=status,
            media_type="application/json",
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        resp = _err(request, exc.status_code, _CODES.get(exc.status_code, "error"), str(exc.detail))
        for k, v in (getattr(exc, "headers", None) or {}).items():
            resp.headers[k] = v
        return resp

    @app.exception_handler(RequestValidationError)
    async def _val_exc(request: Request, exc: RequestValidationError):
        # never echo the offending input back (could be large or sensitive)
        details = [
            {"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()[:20]
        ]
        return _err(request, 422, "validation_error", "request validation failed", details)

    @app.exception_handler(derive.ExpressionError)
    async def _expr_exc(request: Request, exc):
        return _err(request, 422, "invalid_expression", str(exc))

    @app.exception_handler(derive.AlignmentError)
    async def _align_exc(request: Request, exc):
        return _err(request, 422, "alignment_error", str(exc))

    @app.exception_handler(derive_service.DerivationConflict)
    async def _conflict_exc(request: Request, exc):
        return _err(request, 409, "conflict", str(exc))

    @app.exception_handler(DependencyError)
    async def _dep_exc(request: Request, exc: DependencyError):
        return _err(
            request,
            409,
            "has_dependents",
            f"series {exc.series_id!r} is a source of derived series; "
            "delete them first or pass cascade=true",
            {"dependents": exc.dependents},
        )

    @app.exception_handler(KeyError)
    async def _key_exc(request: Request, exc: KeyError):
        # a series/rule vanished between the existence check and the read (concurrent delete)
        return _err(request, 404, "not_found", "unknown series (it may have just been deleted)")

    @app.exception_handler(BudgetError)
    async def _budget_exc(request: Request, exc: BudgetError):
        return _err(request, 422, "export_budget_exceeded", str(exc))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        rid = request.scope.get("state", {}).get("request_id")
        logger.error("request_id=%s unhandled %s", rid, type(exc).__name__, exc_info=exc)
        return _err(request, 500, "internal_error", "internal server error")

    # ---- dependencies ---------------------------------------------------------------------
    def require_write(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
        if key is None:
            raise HTTPException(403, "service is read-only (AILAB_API_KEY not configured)")
        if x_api_key is None or not secrets.compare_digest(x_api_key.encode(), key.encode()):
            raise HTTPException(401, "invalid or missing X-API-Key")

    def _meta(series_id: str) -> SeriesMeta:
        try:
            return store.get_meta(series_id)
        except KeyError:
            raise HTTPException(404, f"unknown series {series_id!r}") from None

    def _page(items: list, total: int, limit: int, offset: int) -> dict:
        nxt = offset + limit
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": nxt if nxt < total else None,
        }

    # ---- system -------------------------------------------------------------------------
    @app.get("/health", tags=["system"], summary="Liveness + light DB check")
    def health() -> dict:
        """Liveness; includes version and a database check. Does not run models."""
        try:
            t0 = time.perf_counter()
            db_ok = store.db_check()
            n = store.count_series()
            ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception:
            db_ok, n, ms = False, -1, None
        return {
            "status": "ok" if db_ok else "degraded",
            "version": __version__,
            "uptime_s": round(time.time() - started, 1),
            "db": {"engine": "duckdb", "ok": db_ok, "latency_ms": ms},
            "store": {"engine": "duckdb", "ok": db_ok, "series": n},
            "writable": key is not None,
        }

    @app.get(
        "/ready",
        tags=["system"],
        summary="Readiness (DB reachable and demo preload finished)",
        responses={503: {"description": "Not ready"}},
    )
    def ready():
        checks = {"db": False, "schema": False}
        try:
            checks["db"] = store.db_check()
            store.count_series()
            checks["schema"] = True
        except Exception:
            pass
        ok = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "version": __version__, "checks": checks},
            status_code=200 if ok else 503,
        )

    r = APIRouter(prefix="/api/v1", responses=ERR)

    # ---- series -------------------------------------------------------------------------
    @r.get("/series", tags=["series"], summary="List series (paginated)")
    def list_series(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        indicator_type: Literal["count", "proportion", "rate", "event"] | None = None,
        derived: bool | None = Query(None, description="true = only derived series"),
    ) -> Page:
        items, total = store.page_series(limit, offset, indicator_type, derived)
        return _page([m.model_dump() for m in items], total, limit, offset)

    @r.post(
        "/series",
        tags=["series"],
        dependencies=[Depends(require_write)],
        status_code=201,
        summary="Create a base series (write)",
    )
    def create_series(body: SeriesIn, overwrite: bool = False) -> dict:
        with write_lock:
            m = body.meta
            if "indicator_type" not in m.model_fields_set:
                raise HTTPException(
                    422, "meta.indicator_type is required (count|proportion|rate|event)"
                )
            if m.derived_expr or m.derived_from or m.bindings or m.lineage:
                raise HTTPException(422, "lineage fields are reserved; use POST /api/v1/derive")
            if len(body.observations) > cfg.max_observations:
                raise HTTPException(422, f"at most {cfg.max_observations} observations per series")
            try:
                existing = store.get_meta(m.series_id)
            except KeyError:
                existing = None
                if store.count_series() >= cfg.max_series:
                    raise HTTPException(409, f"series limit reached ({cfg.max_series})") from None
            if existing is not None and (not overwrite or existing.is_derived):
                raise HTTPException(
                    409,
                    f"series {m.series_id!r} exists"
                    + (
                        " (derived series cannot be overwritten)"
                        if existing.is_derived
                        else "; pass overwrite=true to replace"
                    ),
                )
            df = pd.DataFrame([{"ds": o.ds, "y": o.y} for o in body.observations])
            try:
                n = store.upsert_series(m, df)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            cache.clear()
            return {
                "series_id": m.series_id,
                "observations": n,
                "replaced": existing is not None,
                "stale_dependents": store.dependents(m.series_id),
            }

    @r.get("/series/{series_id}", tags=["series"], summary="Series metadata")
    def get_series(series_id: str = SeriesId) -> SeriesMeta:
        return _meta(series_id)

    @r.delete(
        "/series/{series_id}",
        tags=["series"],
        dependencies=[Depends(require_write)],
        summary="Delete a series (write)",
    )
    def delete_series(
        series_id: str = SeriesId,
        cascade: bool = Query(False, description="also delete derived series built from it"),
    ) -> dict:
        with write_lock:
            _meta(series_id)
            ids = store.delete_series(series_id, cascade=cascade)
            cache.clear()
            return {"deleted": ids}

    def _data(series_id, start, end, limit, offset) -> dict:
        meta = _meta(series_id)
        total = store.count_observations(
            series_id, str(start) if start else None, str(end) if end else None
        )
        df = store.get_observations(
            series_id, str(start) if start else None, str(end) if end else None, limit, offset
        )
        return {
            "series_id": series_id,
            "indicator_type": meta.indicator_type,
            "value_scale": meta.value_scale,
            **_page(
                [
                    {"ds": d.date().isoformat(), "y": None if pd.isna(v) else float(v)}
                    for d, v in zip(df["ds"], df["y"], strict=True)
                ],
                total,
                limit,
                offset,
            ),
        }

    @r.get("/series/{series_id}/data", tags=["series"], summary="Observations (paginated)")
    def series_data(
        series_id: str = SeriesId,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: int = Query(1000, ge=1, le=MAX_PAGE),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return _data(series_id, start, end, limit, offset)

    @r.get(
        "/series/{series_id}/observations",
        tags=["series"],
        deprecated=True,
        summary="Alias of /data",
    )
    def observations(
        series_id: str = SeriesId,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: int = Query(1000, ge=1, le=MAX_PAGE),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return _data(series_id, start, end, limit, offset)

    @r.get("/series/{series_id}/quality", tags=["series"], summary="Data-quality checklist")
    def series_quality(series_id: str = SeriesId) -> dict:
        return quality.check_series(_meta(series_id), store.get_observations(series_id))

    @r.get("/series/{series_id}/lineage", tags=["series", "derive"], summary="Lineage")
    def lineage(series_id: str = SeriesId) -> dict:
        m = _meta(series_id)
        return {
            "series_id": series_id,
            "is_derived": m.is_derived,
            "derived_expr": m.derived_expr,
            "derived_from": m.derived_from,
            "bindings": m.bindings,
            "lineage": m.lineage,
            "dependents": store.dependents(series_id),
        }

    @r.get(
        "/series/{series_id}/export",
        tags=["series", "export"],
        summary="Download one series (csv | json | parquet)",
        response_class=Response,
    )
    def export_one(
        series_id: str = SeriesId, format: Literal["csv", "json", "parquet"] = "csv"
    ) -> Response:
        meta = _meta(series_id)
        df = store.get_observations(series_id)
        out = pd.DataFrame(
            {"series_id": series_id, "period_start": df["ds"].dt.date, "value": df["y"]}
        )
        fn = f'attachment; filename="{series_id}.{format}"'
        if format == "csv":
            body, mt = out.to_csv(index=False).encode(), "text/csv"
        elif format == "json":
            import json

            doc = {
                "series": meta.model_dump(),
                "observations": [
                    {"period_start": str(d), "value": None if pd.isna(v) else float(v)}
                    for d, v in zip(out["period_start"], out["value"], strict=True)
                ],
            }
            body, mt = json.dumps(doc, ensure_ascii=False).encode(), "application/json"
        else:
            buf = io.BytesIO()
            out.to_parquet(buf, index=False)
            body, mt = buf.getvalue(), "application/vnd.apache.parquet"
        return Response(body, media_type=mt, headers={"Content-Disposition": fn})

    # ---- derived -----------------------------------------------------------------------
    def _plan(body: DeriveIn) -> derive_service.Derivation:
        return derive_service.plan(
            store,
            body.series_id,
            body.name,
            body.expr,
            body.bindings,
            body.align,
            body.indicator_type,
        )

    @r.post(
        "/derive",
        tags=["derive"],
        dependencies=[Depends(require_write)],
        status_code=201,
        summary="Create a derived series (write)",
    )
    def derive_series(body: DeriveIn) -> dict:
        """Evaluate a safe expression over stored series, infer its kind, align the sources
        (missing stays missing), and store the result with its lineage."""
        with write_lock:
            if store.count_series() >= cfg.max_series and not _exists(body.series_id):
                raise HTTPException(409, f"series limit reached ({cfg.max_series})")
            d = derive_service.create_derived(
                store,
                body.series_id,
                body.name,
                body.expr,
                body.bindings,
                body.align,
                body.indicator_type,
                overwrite=body.overwrite,
            )
            cache.clear()
            return _derivation_out(d, stored=True)

    def _exists(sid: str) -> bool:
        try:
            store.get_meta(sid)
            return True
        except KeyError:
            return False

    @r.post(
        "/derive/preview",
        tags=["derive"],
        summary="Dry run: infer kind + alignment, store nothing",
    )
    def derive_preview(body: DeriveIn) -> dict:
        return _derivation_out(_plan(body), stored=False)

    def _derivation_out(d: derive_service.Derivation, stored: bool) -> dict:
        df = d.data
        sample = [
            {"ds": a.date().isoformat(), "y": None if pd.isna(b) else round(float(b), 6)}
            for a, b in zip(
                df["ds"].head(5).tolist() + df["ds"].tail(5).tolist(),
                df["y"].head(5).tolist() + df["y"].tail(5).tolist(),
                strict=True,
            )
        ]
        return {
            "stored": stored,
            "series": d.meta.model_dump(),
            "observations": int(len(df)),
            "n_missing": int(df["y"].isna().sum()),
            "inferred_indicator_type": d.meta.indicator_type,
            "warnings": d.warnings,
            "sample": sample,
        }

    @r.post(
        "/series/{series_id}/recompute",
        tags=["derive"],
        dependencies=[Depends(require_write)],
        summary="Recompute a derived series from its current sources (write)",
    )
    def recompute(series_id: str = SeriesId) -> dict:
        with write_lock:
            _meta(series_id)
            d = derive_service.recompute(store, series_id)
            cache.clear()
            return _derivation_out(d, stored=True)

    # ---- forecast ----------------------------------------------------------------------
    def _analysis(series_id: str, horizon: int) -> dict:
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
                    while len(cache) > CACHE_MAX:
                        cache.popitem(last=False)
        return cache[ck]

    @r.get(
        "/series/{series_id}/forecast", tags=["forecast"], summary="Backtest + policy + forecast"
    )
    def forecast(series_id: str = SeriesId, horizon: int = Query(8, ge=1, le=26)) -> dict:
        """``policy.forecast_allowed=false`` => no forecast values, only the reason."""
        res = _analysis(series_id, horizon)
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

    @r.get("/series/{series_id}/policy", tags=["forecast"], summary="Forecast policy only")
    def policy(series_id: str = SeriesId, horizon: int = Query(8, ge=1, le=26)) -> dict:
        return _analysis(series_id, horizon)["policy"]

    # ---- alerts ------------------------------------------------------------------------
    @r.get("/alerts/rules", tags=["alerts"], summary="List alert rules")
    def list_rules(
        series_id: str | None = Query(None, pattern=SERIES_ID_PATTERN),
    ) -> list[AlertRule]:
        return store.list_rules(series_id)

    @r.put(
        "/alerts/rules",
        tags=["alerts"],
        dependencies=[Depends(require_write)],
        summary="Create/replace an alert rule (write)",
    )
    def put_rule(rule: AlertRule) -> AlertRule:
        _meta(rule.series_id)
        store.upsert_rule(rule)
        return rule

    @r.delete(
        "/alerts/rules/{rule_id}",
        tags=["alerts"],
        dependencies=[Depends(require_write)],
        summary="Delete an alert rule (write)",
    )
    def delete_rule(rule_id: str = PathParam(..., pattern=SERIES_ID_PATTERN)) -> dict:
        if not store.delete_rule(rule_id):
            raise HTTPException(404, "unknown rule")
        return {"deleted": rule_id}

    @r.get("/series/{series_id}/alerts", tags=["alerts"], summary="Ad-hoc alert scan")
    def series_alerts(
        series_id: str = SeriesId,
        method: Literal["ears_c1", "ears_c2", "ears_c3", "cusum"] = "ears_c2",
        baseline: int = Query(7, ge=3, le=104),
    ) -> dict:
        """Statistical-signal scan (no stored rule needed). Not an outbreak declaration."""
        _meta(series_id)
        g, _ = quality.model_input(store.get_observations(series_id))
        return {
            "series_id": series_id,
            "method": method,
            **alerts.summarise(alerts.detect(g, method, baseline)),
        }

    @r.get("/alerts/evaluate", tags=["alerts"], summary="Evaluate stored rules")
    def evaluate_rules(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Page:
        rules = [x for x in store.list_rules() if x.enabled]
        out = []
        for rule in rules[offset : offset + limit]:
            g, _ = quality.model_input(store.get_observations(rule.series_id))
            out.append(
                {"rule": rule.model_dump(), **alerts.summarise(alerts.evaluate_rule(rule, g))}
            )
        return _page(out, len(rules), limit, offset)

    # ---- export ------------------------------------------------------------------------
    @r.post(
        "/export",
        tags=["export"],
        dependencies=[Depends(require_write)],
        summary="Write the static export to AILAB_OUT (write)",
    )
    def export() -> dict:
        """Manifest + per-series JSON + Parquet, written server-side to ``AILAB_OUT``."""
        with compute_lock:
            return export_site(store, cfg.out_dir)

    app.include_router(r)
    return app
