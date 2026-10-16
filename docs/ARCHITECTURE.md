# Architecture

```
            ┌────────────── ailab serve (one process) ──────────────┐
 client ──► │ ASGI middleware: request-id/access log · body-size cap │
 (curl,     │ · CORS (optional)                                      │
  site,     │ FastAPI routes /api/v1/*  ── auth dependency (X-API-Key)│
  CLI)      │    │                                                    │
            │    ├─ write_lock  (create/derive/delete/recompute)      │
            │    ├─ compute_lock + LRU cache (forecast/policy)        │
            │    ▼                                                    │
            │ SeriesStore (DuckDB, one connection, RLock)             │
            │   tables: series, observations, alert_rules,            │
            │           series_lineage                                │
            └───────────────┬─────────────────────────────────────────┘
                            ▼
                 /data/ailab.duckdb   (volume)        /out  (static export)
```

## Modules (`src/ailab`)

| module | role |
|---|---|
| `schema.py` | pydantic contract: `SeriesMeta`, `ForecastPolicy`, `AlertRule`; id regex `^[a-z0-9][a-z0-9_]{0,63}$` |
| `store.py` | DuckDB store; validates dates (one weekly grid, no duplicates), values (kind ranges, finite), stores NULL for missing; lineage edges; Parquet I/O |
| `derive.py`, `derive_service.py` | expression parser (AST whitelist + `simpleeval`), typed series with kind inference, alignment, lineage; create/preview/recompute |
| `quality.py` | regularise to a weekly grid, interpolate ≤ 2-week gaps for model input, checklist and grade |
| `transforms.py`, `forecast.py` | `log1p`/`logit`, statsforecast models, rolling-origin backtest, conformal-style intervals |
| `policy.py` | the decision table in [MODEL_POLICY.md](MODEL_POLICY.md) |
| `alerts.py` | EARS C1–C3, CUSUM |
| `pipeline.py` | `analyse`: quality → backtest → policy → forecast → alerts (history cap 840 weeks) |
| `export.py` | static export with size budgets |
| `api/` | FastAPI app, middleware (request id, size limit, log redaction), settings from env |
| `cli.py` | Typer CLI (`serve`, `demo`, `series`, `derive`, `synth`, …) |
| `synth.py`, `testset.py` | seeded synthetic data; the reusable API test-data set + loader |
| `sources/`, `data/` | source registry (licences) and the bundled CDC FluView fixture |

## Concurrency model

* FastAPI runs the (synchronous) handlers in a thread pool. The DuckDB connection is shared and
  guarded by an `RLock` inside `SeriesStore`.
* `write_lock` makes check-then-write sequences atomic: two simultaneous `POST /series` for the same
  id without `overwrite` give exactly one `201` and `409` for the rest.
* `compute_lock` serialises CPU-heavy analysis (predictable memory, one core). Results are cached
  (64 entries) keyed by series id, horizon, a hash of the data and the metadata; every write clears
  the cache. Reads, `/health` and writes do not wait for a running forecast.
* One process per database file (DuckDB limitation). Scale reads/CPU by running separate
  instances on separate databases, or export static files and serve those.

## Failure behaviour

All errors are JSON `{"error": {code, message, request_id, details?}}`. Validation details never echo
the input; unhandled exceptions return `internal_error` without a traceback and are logged with the
request id. Models are never persisted (no pickle); forecasts are recomputed from data.
