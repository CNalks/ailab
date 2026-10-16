# Testing

| command | what | time |
|---|---|---|
| `make test` (`uv run pytest`) | ~300 unit/API tests with FastAPI's `TestClient`, synthetic data, no network | ~25 s |
| `make lint` | `ruff check` + `ruff format --check` | 2 s |
| `make e2e` | **black-box** suite (~520 tests): starts real `ailab serve` processes and talks HTTP | ~1.5 min |
| `make e2e-slow` | adds the slow performance tests (10/20/50/100-year weekly series, whole-set export) | +1 min |
| `make e2e-container` | the same suite against a container built from the `Dockerfile` (read-only fs, non-root, volumes) | ~2.5 min |
| `make e2e-report` | markdown coverage table from `out/e2e-*.json` | – |

Results and findings: [TEST_REPORT.md](TEST_REPORT.md).

## Synthetic test data
`ailab.synth` generates seeded weekly series (seasonality, trend, negative-binomial counts, binomial
proportions with varying denominators, outbreak spikes, missing weeks as NULL). `ailab.testset` builds on it:

```bash verify
ailab synth testset --out /tmp/ailab-testset
ls /tmp/ailab-testset | head -3
jq -r '.cases | map(select(.expect=="accept")) | length' /tmp/ailab-testset/manifest.json
```

55 cases: 20 valid (count and proportion fractions/percent, seasonal, missing weeks, outbreak spikes, trailing
gap, rate, sparse event, positives/tests pair, short, single point, constant, all-zero, all-missing, huge values,
10-year, 100-year (5200 weeks), unicode names, plus **one real** CDC FluView slice: the last 156 weeks of the bundled
fixture) and 35 invalid (negative counts, proportions out of range, irregular and daily dates, duplicate
timestamps, `NaN`/`Infinity`/`1e999`, bad dates and formats, non-numeric values, empty observations, missing or
unknown kind, injection-style ids, over-long names, bad season length, reserved lineage fields, too many
observations). `tests/data/synthetic/` holds the generated files and `manifest.json` (expectation per case);
a unit test keeps them in sync with the generator. Load them into a running service through the REST API:

```
ailab synth load --url http://127.0.0.1:8000 --key $AILAB_API_KEY            # generate + load + check
ailab synth load --url … --key … --dir tests/data/synthetic                 # from the committed files
```

## The e2e suite (`tests/e2e`)
`harness.py` starts the service as a subprocess (`TARGET=local`, default) or as a container
(`AILAB_E2E_TARGET=container`, with `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`
and named volumes; `AILAB_E2E_ENGINE=podman|docker`, `AILAB_E2E_IMAGE=ailab:local`). Several instances are used:
main (key, demo data, CORS), read-only (no key), tight limits, export, persistence. Every HTTP call is
recorded so the report can build the endpoint × scenario table.

Scenarios: `test_system` (health, ready, docs, OpenAPI incl. staleness check, error shape, request ids) ·
`test_auth` (missing/wrong/short/odd keys on every write endpoint, read-only mode) · `test_series`
(CRUD, pagination limits, filters, formats, idempotency, validation, unicode) · `test_testset` (all 55 cases,
policy and forecast invariants) · `test_derive` (kind inference, alignment, recompute, cascade, ~100 hostile
expressions) · `test_alerts` · `test_forecast` (parameters, cache, responsiveness, timings) · `test_export` ·
`test_robustness` (413, caps, CORS, injection-style ids/params, malformed JSON, NaN/inf, slow client, log hygiene) ·
`test_concurrency` (parallel reads/writes, races) · `test_persistence` (graceful restart, `kill -9`, volume
re-create) · `test_config` (bad configuration, container hardening) · `test_docs_samples` (executes the
`bash verify` / `python verify` blocks of README*.md and docs/*.md).

Code samples tagged plain `bash` (e.g. `docker run …`) are not executed by the suite; they are covered by the
CI `docker` job (build, smoke test, e2e against the image) and were run by hand for each release.
