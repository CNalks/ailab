# Service API (`ailab serve`)

OpenAPI/Swagger: `/docs`, schema `/openapi.json` (committed copy: [openapi.json](openapi.json), regenerate with
`make openapi`; the e2e suite fails if it is stale). JSON in/out. Base: `/api/v1`. Hands-on walk-through:
[TUTORIAL.md](TUTORIAL.md). Every operation is exercised by the black-box suite, see [TEST_REPORT.md](TEST_REPORT.md).

| method & path | auth | purpose |
|---|---|---|
| `GET /health` | – | liveness: `status`, `version`, `uptime_s`, `db {ok, latency_ms}`, `store.series`, `writable` |
| `GET /ready` | – | readiness: 200 `ready` / 503 `not_ready` (DB reachable + schema) |
| `GET /api/v1/series?limit&offset&indicator_type&derived` | – | list (paginated; limit 1..200, default 50) |
| `POST /api/v1/series[?overwrite]` | key | create base series (`meta.indicator_type` required; ≤ 5200 observations; null = missing) |
| `GET /api/v1/series/{id}` | – | metadata |
| `DELETE /api/v1/series/{id}[?cascade]` | key | delete (409 if derived series depend on it) |
| `GET /api/v1/series/{id}/data?start&end&limit&offset` | – | observations (limit 1..5000, default 1000); `/observations` = deprecated alias |
| `GET /api/v1/series/{id}/quality` | – | data-quality checklist |
| `GET /api/v1/series/{id}/lineage` | – | derivation, sources, alignment, dependents |
| `GET /api/v1/series/{id}/forecast?horizon` | – | backtest + policy + forecast (horizon 1..26); first call takes seconds (backtest), then cached; models see the last 840 weeks only |
| `GET /api/v1/series/{id}/policy?horizon` | – | policy only |
| `GET /api/v1/series/{id}/alerts?method&baseline` | – | ad-hoc EARS C1–C3 / CUSUM scan |
| `GET /api/v1/series/{id}/export?format=csv\|json\|parquet` | – | download one series |
| `POST /api/v1/derive` | key | create derived series (`series_id, name, expr, bindings?, align?, indicator_type?, overwrite?`) |
| `POST /api/v1/derive/preview` | – | dry run: inferred kind, alignment, warnings; stores nothing |
| `POST /api/v1/series/{id}/recompute` | key | recompute a derived series |
| `GET /api/v1/alerts/rules[?series_id]`, `PUT /api/v1/alerts/rules`, `DELETE /api/v1/alerts/rules/{rule_id}` | – / key / key | alert rules |
| `GET /api/v1/alerts/evaluate?limit&offset` | – | evaluate stored rules (paginated) |
| `POST /api/v1/export` | key | static export (manifest + JSON + Parquet) into `AILAB_OUT` |

Paginated responses: `{items, total, limit, offset, next_offset}`.

## Auth
Reads are open (put a reverse proxy/auth in front before exposing publicly). **Every state-changing
endpoint** (create/delete/derive/recompute/rules/export) needs header `X-API-Key: <AILAB_API_KEY>`.
If `AILAB_API_KEY` is unset the service is **read-only** (writes ⇒ 403); there is no default key;
a key shorter than 16 characters refuses to start (`openssl rand -hex 24`). Comparison is
constant-time. Analysis is serialised in-process and cached (64 entries); create/derive/delete/recompute are serialised too, so concurrent creates of one id give exactly one 201.

## Errors
Always `{"error": {"code", "message", "request_id", "details"?}}`, also for 404/405/422/500; the
request id is also in header `X-Request-ID` and the server log. Codes: `bad_request`, `unauthorized`
(401), `forbidden` (403, read-only), `not_found`, `conflict`, `has_dependents` (409, with
`details.dependents`), `payload_too_large` (413), `validation_error` (422, `details` lists
location/message/type but **never echoes the input**), `invalid_expression`, `alignment_error`
(422), `export_budget_exceeded` (422, `POST /export` size budget), `internal_error` (500, no traceback or exception text to the client).
Malformed JSON (e.g. invalid UTF-8) may be answered `400 bad_request` instead of 422.

## Configuration (environment)
| variable | default | meaning |
|---|---|---|
| `AILAB_DB` | in-memory | DuckDB file (`/data/ailab.duckdb` in the image) |
| `AILAB_API_KEY` | unset ⇒ read-only | write key, ≥ 16 chars |
| `AILAB_PRELOAD_DEMO` | `0` (image: `1`) | load the bundled FluView demo into an empty DB |
| `AILAB_OUT` | `out` | static-export directory |
| `AILAB_MAX_BODY_BYTES` | 2 MiB | request body limit (413); Content-Length and chunked bodies |
| `AILAB_MAX_OBSERVATIONS` | 5200 | per series on create |
| `AILAB_MAX_SERIES` | 500 | series in the store |
| `AILAB_CORS_ORIGINS` | none (CORS off) | comma-separated allowed origins; no credentials, methods GET/POST/PUT/DELETE |
| `AILAB_LOG_LEVEL` | `INFO` | |

Logs: one line per request (`request_id METHOD path -> status ms`); no headers, bodies or query
strings; the configured key is also redacted from any log message. Models are never persisted
(no pickle/joblib anywhere; a test enforces it) — forecasts are recomputed from data.
