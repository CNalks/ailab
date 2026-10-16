# ailab

[![CI](https://github.com/CNalks/ailab/actions/workflows/ci.yml/badge.svg)](https://github.com/CNalks/ailab/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Image: ghcr.io/cnalks/ailab](https://img.shields.io/badge/image-ghcr.io%2Fcnalks%2Failab-2496ED?logo=docker&logoColor=white)](https://github.com/CNalks/ailab/pkgs/container/ailab)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)

**English** · [简体中文](README.zh-CN.md)

A small, reproducible **time-series engine for infectious-disease surveillance data**: store weekly
series, derive new ones with safe expressions, forecast **only when a backtest says the forecast is
honest** (every forecast carries a machine-readable policy), raise statistical alert signals, and export
everything as static JSON/Parquet. Use it as a **library**, a **CLI / offline batch tool**, or a
**Docker-packaged REST service** (FastAPI + embedded DuckDB — no Postgres, Redis, Celery or Supabase).

> **Not for clinical or public-health decisions.** Forecasts and alerts are statistical analyses of
> public data, not official products. See [Limitations](#limitations-and-honest-caveats).

## Quick start

```bash
# a service with demo data, read-only (no API key => writes answer 403)
docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/cnalks/ailab

# the same, writable: choose a key of at least 16 characters
export AILAB_API_KEY=$(openssl rand -hex 24)
docker run --rm -p 127.0.0.1:8000:8000 -e AILAB_API_KEY ghcr.io/cnalks/ailab

# harden it (what the test suite runs against), keep data in a volume:
docker run -d --name ailab -p 127.0.0.1:8000:8000 --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges -e AILAB_API_KEY -v ailab-data:/data ghcr.io/cnalks/ailab
```

or `docker compose up` (see [docker-compose.yml](docker-compose.yml)). Then:

```bash verify
export AILAB_URL=${AILAB_URL:-http://127.0.0.1:8000}
curl -fsS "$AILAB_URL/health" | jq -c '{status, version}'
curl -fsS "$AILAB_URL/api/v1/series/us_ilinet_wili/policy" | jq -c '{forecast_allowed, reason, model_family, horizon_weeks}'
```

```
{"status":"ok","version":"0.3.0"}
{"forecast_allowed":true,"reason":"beats_seasonal_naive_in_rolling_origin_backtest","model_family":"mstl_ets","horizon_weeks":8}
```

A static, trilingual showcase with five precomputed real series lives in [`site/`](docs/SHOWCASE_SITE.md).

Interactive API docs: <http://127.0.0.1:8000/docs>. **Continue with the
[5-minute tutorial](docs/TUTORIAL.md)** (create series, derive, forecast, alerts, export — every
command in it is executed by the test suite).

Without Docker (Python 3.12 and [uv](https://docs.astral.sh/uv/)):

```bash
git clone https://github.com/CNalks/ailab && cd ailab
uv sync && uv run pytest              # ~20 s
uv run ailab serve                     # http://127.0.0.1:8000  (add --preload-demo for the demo data)
uv run ailab demo --out out            # offline batch: static export of the demo series
```

## What it does

| Capability | Where |
|---|---|
| Series store: DuckDB file or in-memory, Parquet import/export; **missing = NULL, never 0**; values validated against the series kind | `ailab.store` |
| Explicit **series kinds** `count` / `proportion` / `rate` / `event` (transform, bounds, aggregation, forecast strictness) | [SERIES_KINDS.md](docs/SERIES_KINDS.md) |
| **Derived series** from safe expressions (`A / B`, `rolling_ratio`, `lag`, …) with stored lineage | [DERIVED_SERIES.md](docs/DERIVED_SERIES.md) |
| Data-quality checklist → grade A/B/C | [MODEL_POLICY.md](docs/MODEL_POLICY.md#data-quality-grade) |
| Forecast: SeasonalNaive baseline vs Naive / MSTL-ETS / Theta with a rolling-origin backtest, conformal-style intervals | [METHODS.md](docs/METHODS.md) |
| **Forecast policy** (when a forecast is allowed, how it is shown) | [MODEL_POLICY.md](docs/MODEL_POLICY.md) |
| **Quality gate (v0.3)**: refuses / limits forecasts of low-quality series (too short, gappy, constant, stale, unstable, no skill vs. baseline …) with machine-readable reason codes and en/zh/fr messages; `GET /series/{id}/gate`, `ailab gate <id>` | [MODEL_POLICY.md](docs/MODEL_POLICY.md#quality-gate-v03), [BENCHMARK.md](docs/BENCHMARK.md) |
| Alert signals: EARS C1/C2/C3, CUSUM, Farrington-style seasonal alert, alert gate, stored rules | [MODEL_POLICY.md](docs/MODEL_POLICY.md#alert-signals) |
| REST API, 23 operations, OpenAPI | [API.md](docs/API.md), [openapi.json](docs/openapi.json) |
| Static export (`manifest.json`, per-series JSON, Parquet) | [EXPORT_FORMAT.md](docs/EXPORT_FORMAT.md) |
| Reusable synthetic test data + loader | [TESTING.md](docs/TESTING.md) |

## Concepts

**Series kinds.** Every series has an explicit `indicator_type`, required on creation.
`count` – non-negative tallies; `proportion` – part/whole within `[0, value_scale]` (`value_scale` 1 =
fractions, 100 = percent); `rate` – any other measure (non-negative, or signed when produced by `-`/`diff`);
`event` – sparse notifiable events (monitored, never forecast). Models run on a kind-specific
transform (`log1p` for count/rate/event, `logit` for proportions) so forecasts and interval ends stay
inside the valid range. Proportions must be aggregated as a **ratio of sums**, never averaged, and are
held to a stricter forecast standard. Details: [SERIES_KINDS.md](docs/SERIES_KINDS.md).

**Derived series.** `POST /api/v1/derive` evaluates an expression over stored series, for example
`positives / tests` (→ proportion), `100 * A / B` (→ percent), `rolling_ratio(pos, tests, 4)`,
`rolling_mean(cases, 7)`, `lag(A, 52)`, `sum(a, b, c)`. Evaluation uses a whitelist over the Python AST
plus the `simpleeval` library — no `eval`/`exec`. The result kind is inferred (and may be asserted),
missing weeks stay missing, `x/0` is missing (never ∞), and the result stores its **lineage** (sources,
alignment report, kind-inference trail). A derived series is a snapshot until `POST …/recompute`.

**Forecast policy.** Each forecast response carries:

| field | meaning |
|---|---|
| `forecast_allowed` | `false` ⇒ no forecast values at all, only `reason` |
| `reason` | machine-readable cause, e.g. `beats_seasonal_naive_in_rolling_origin_backtest`, `history_lt_3_seasons`, `data_gaps_too_long`, `sparse_series`, `no_skill_vs_seasonal_naive`, `indicator_type_event_sparse`, `band_undercoverage_in_backtest` |
| `display_mode` | `forecast_band` (point + 80/95 % band) · `forecast_point_only` (band withheld: poor backtest coverage) · `monitor_only` · `history_only` |
| `model_family` | `mstl_ets` · `theta` · `naive` · `seasonal_naive` · `none` |
| `horizon_weeks` | how many leading weeks beat the seasonal-naive baseline in the backtest (≤ the requested horizon) |
| `indicator_type` | kind the rules were applied for |
| `warnings` | non-fatal caveats (short backtest, truncated horizon, grade B data, history cap …) |
| `gate_decision`, `reason_codes`, `limited` | (0.3) quality-gate outcome `allowed`/`limited`/`refused` and machine-readable reasons; the full `gate` object is a sibling of `policy` |

A candidate model must beat the **seasonal-naive** baseline in a rolling-origin backtest (default: ≥ 3
seasons, models refit at every origin using only earlier data). See [MODEL_POLICY.md](docs/MODEL_POLICY.md).

**Alert rules.** EARS C1/C2/C3 (CDC) and a standardised CUSUM compare each week with a baseline of the
previous weeks (default 7). They are **statistical signals**, not outbreak declarations, carry no
risk colours, and EARS/CUSUM do not model seasonality (a normal seasonal rise will signal); the Farrington-style alert does and is the default for seasonal counts. Use ad-hoc scans
(`GET /series/{id}/alerts`) or stored rules (`PUT /alerts/rules`, `GET /alerts/evaluate`).

**Data-quality scoring.** A transparent checklist (length ≥ 3 seasons, completeness, longest gap ≤ 2
weeks, ≥ 50 % non-zero, zero runs, negatives) → grade **A** (all pass), **B** (only warnings),
**C** (a blocking check fails → no forecast). Not a black-box score.

**Export format.** `manifest.json` + `series/<id>.json` (data, quality, policy, backtest, forecast,
alerts) + `downloads/<id>.parquet` + `sources.json`, with size budgets: [EXPORT_FORMAT.md](docs/EXPORT_FORMAT.md).
Single series can also be downloaded as CSV/JSON/Parquet via the API.

**Auth model.** Reads are open. **Every state-changing endpoint** (create/delete series, derive,
recompute, alert rules, export) requires the header `X-API-Key` equal to `AILAB_API_KEY` (≥ 16
characters, compared in constant time). Without `AILAB_API_KEY` the service is **read-only**
(writes → 403); there is no default key; a shorter key refuses to start. Put TLS, rate limiting and
authentication for reads in a reverse proxy before exposing the service.

## Configuration (environment variables)

| variable | default | meaning |
|---|---|---|
| `AILAB_DB` | in-memory (`/data/ailab.duckdb` in the image) | DuckDB file; one process per file |
| `AILAB_API_KEY` | unset ⇒ read-only | write key, ≥ 16 characters |
| `AILAB_PRELOAD_DEMO` | `0` (image: `1`) | load the bundled CDC FluView demo into an empty database |
| `AILAB_OUT` | `out` (image: `/out`) | directory for `POST /api/v1/export` |
| `AILAB_MAX_BODY_BYTES` | 2 MiB | request body limit (413) |
| `AILAB_MAX_OBSERVATIONS` | 5200 | observations per series on create |
| `AILAB_MAX_SERIES` | 500 | series in the store |
| `AILAB_CORS_ORIGINS` | empty (CORS off) | comma-separated allowed origins |
| `AILAB_LOG_LEVEL` | `INFO` | one access-log line per request, no headers/bodies/queries/keys |

## Resource usage (measured)

Image **660 MB** (CPU only, non-root uid 10001). Process RSS ≈ **290–300 MB** with the demo data after
several forecasts (also after a 5200-week series). Analysis is single-threaded and serialised
(one forecast at a time; reads, health and writes stay responsive). Forecast wall time on one core
(first call; repeats are cached): 6 years weekly ≈ 2 s, 10 years ≈ 4 s, anything ≥ 16 years ≈ 7–8 s
because models only see the most recent 840 weeks. Storage is tiny (a few KB per series).
Measurements and method: [TEST_REPORT.md](docs/TEST_REPORT.md).

## CLI

```
ailab serve [--host --port --db --preload-demo]         # REST service
ailab demo [--refresh] [--out out]                       # demo series -> static export
ailab series add|list|show|data|delete --db my.duckdb   # manage base series (kind required)
ailab derive preview|create|recompute --db my.duckdb    # derived series
ailab list | check <id> | gate <id> | forecast <id> --h 8 | alert <id> --method ears_c2
ailab export --db my.duckdb --out out                    # static export of your series
ailab synth --kind count --weeks 300 --seed 1 --out x.csv    # one deterministic synthetic series
ailab synth testset --out tests/data/synthetic                # the full API test-data set
ailab synth load --url http://127.0.0.1:8000 --key $AILAB_API_KEY   # load it into a running service
ailab doctor                                              # environment self-check
```

## Limitations and honest caveats

- **Not a decision tool.** Do not use for clinical, public-health or policy decisions. Output is
  unreviewed statistics on public data.
- **The backtest is optimistic.** The model is *selected* on the same rolling-origin backtest that is
  then reported (selection bias); origins overlap (step 3 weeks) so windows are correlated; with ~3 seasons
  there are only ~3 peaks. Reported skill is a guide, not a guarantee.
- **Revisions are ignored.** Backtests use the final (latest-issue) values, not what was known at the time.
  Real-time reporting delays and backfill are not modelled, so real-time skill will be lower.
- **Intervals are marginal.** Empirical quantiles of backtest errors mix peak and off-season weeks; coverage
  is checked leave-one-window-out but is not guaranteed in any given season. Treat bands as indicative.
- **Counts/rates with a backtest shorter than 3 seasons may still be forecast** (with a warning in
  `policy.warnings`); proportions are denied in that case.
- **History cap.** Models fit on the last 840 weeks (~16 years) only, to bound compute time.
- **EARS/CUSUM alerts ignore seasonality**; expect signals every winter (use `farrington`). They flag statistical deviation, not outbreaks.
- **Weekly data only** (one weekday grid per series, `season_length` 2–60), at most 5200 observations
  per series by default.
- **Single-node.** Embedded DuckDB allows one server process per database file; analysis is serialised.
  No clustering, no read authentication, no rate limiting (use a reverse proxy).
- The bundled demo is a fixture from the CDC FluView national series (see below), not live data.

## Data sources and licences

Data are **not** covered by the code licence; each dataset follows its source's terms and every
series carries `licence`, `attribution` and `notice`. Details and status: [DATA_SOURCES.md](docs/DATA_SOURCES.md).
The bundled demo fixture is CDC FluView (ILINet) national weekly data retrieved via the Delphi Epidata
API (public domain, attribution to CDC required; see the file for the exact terms).
*Additional sources: placeholder — none enabled yet.*

## Documentation

[Tutorial](docs/TUTORIAL.md) · [API reference](docs/API.md) ([openapi.json](docs/openapi.json)) ·
[Architecture](docs/ARCHITECTURE.md) · [Model policy](docs/MODEL_POLICY.md) · [Methods](docs/METHODS.md) ·
[Series kinds](docs/SERIES_KINDS.md) · [Derived series](docs/DERIVED_SERIES.md) ·
[Export format](docs/EXPORT_FORMAT.md) · [Deployment](docs/DEPLOYMENT.md) · [FAQ](docs/FAQ.md) ·
[Testing](docs/TESTING.md) · [Test report](docs/TEST_REPORT.md) · [Data sources](docs/DATA_SOURCES.md)

## Contributing and security

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) (setup, `make test`, `make e2e`, style).
Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## Licence

Code: [Apache-2.0](LICENSE), © 2026 [CNalks](https://github.com/CNalks). Data: see above.
