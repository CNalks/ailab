# FAQ

**Why did my forecast request return no numbers?**
`policy.forecast_allowed` is `false`; read `policy.reason` ([table](MODEL_POLICY.md#decision-order-first-match-wins)).
Typical causes: fewer than 3 seasons (156 weekly points for `season_length` 52), gaps longer than 2 weeks,
mostly-zero data, an `event` series, or a model that does not beat the seasonal-naive baseline.

**Is "forecast allowed" a statement that the forecast is accurate?**
No. It says the model beat a naive seasonal baseline in a rolling-origin backtest on this series. See the
[caveats](../README.md#limitations-and-honest-caveats).

**Can I use it for clinical or policy decisions?** No.

**Why do I get 403 on every write?** The service has no `AILAB_API_KEY` → read-only. Start it with a key of ≥ 16
characters and send `X-API-Key`. A wrong key is `401`.

**The service refuses to start: "AILAB_API_KEY must be at least 16 characters".** By design; use
`openssl rand -hex 24`.

**How do I load my own data?** `POST /api/v1/series` (JSON; see the [tutorial](TUTORIAL.md)) or
`ailab series add ID data.csv --name … --kind count --db my.duckdb`. Dates must be week starts on one weekday
grid (all dates the same weekday, no duplicates); `null` = missing. `indicator_type` is required.

**Daily or monthly data?** Not supported; weekly only.

**Why is `count / count` a proportion and where did my values go outside [0, 1]?** The kind is inferred; if
values leave `[0, 1]` the result is downgraded to `rate` with a warning. Use `100 * A / B` for percent.

**How do I average a proportion over several weeks?** As a ratio of sums: `rolling_ratio(pos, tests, 4)`.
`rolling_mean` of a proportion is refused on purpose.

**My series has 5200 points and the forecast uses fewer.** Models fit on the last 840 weeks; the policy
carries a warning. Quality, alerts and downloads use all data.

**The first forecast takes seconds.** The backtest refits several models at ~50 origins. Repeats are served
from a cache (cleared on any write). Forecasts run one at a time.

**`POST /api/v1/export` is slow / returns 422 `export_budget_exceeded`.** It analyses every stored series
(seconds each) and enforces size budgets (60 KB gzip per series, 1.5 MB total). Export a subset with the CLI
(`ailab export --series ID`).

**Can I run several replicas on one volume?** No: DuckDB allows one process per file. Run independent
instances, or export static files.

**Where are the logs?** stdout, one line per request; set `AILAB_LOG_LEVEL`.

**Does it call external services?** Not at run time. `ailab demo --refresh` (CLI only) fetches CDC data
from the Delphi Epidata API.

**How do I get the OpenAPI spec?** `GET /openapi.json` or [docs/openapi.json](openapi.json)
(regenerate with `make openapi`; a test fails if it is stale).
