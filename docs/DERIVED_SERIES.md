# Derived (composite) series

A derived series is computed from existing series by an **expression**, materialised in DuckDB and
stored with its **lineage**. Created via CLI (`ailab derive …`) or REST (`POST /api/v1/derive`).

## Expression language
Numbers, series names (a stored series id, or an alias bound with `--bind A=series_id` /
`"bindings": {"A": "series_id"}`), `+ - * /`, unary `+ -`, parentheses and these functions
(positional arguments only; window/lag arguments must be integer constants 1..520):

| function | result | notes |
|---|---|---|
| `rolling_sum(x, n)`, `rolling_mean(x, n)` (`ma`) | trailing window | all `n` weeks must be present, else missing; **not allowed for proportions** |
| `rolling_ratio(num, den, n)` | `Σnum / Σden` over `n` weeks | ratio of sums; counts/rates only |
| `sum(a, b, …)` | n-ary sum | missing if any term is missing; not for proportions |
| `lag(x[, n])`, `diff(x[, n])`, `pct_change(x[, n])` (`pct`) | shift / difference / percent change | `diff` and `pct_change` give `rate` |
| `abs(x)`, `log1p(x)`, `sqrt(x)` | element-wise | domain errors are errors, not NaN |

Examples: `positives / tests` · `100 * positives / tests` · `A + B` · `rolling_mean(A, 7)` ·
`rolling_ratio(pos, tests, 4)` · `sum(a, b, c)` · `lag(A, 52)` · `sum(a, b) / sum(c, d)`.
Max 300 characters, 80 AST nodes, 8 source series.

## Safety
Parsing and evaluation use the maintained [`simpleeval`](https://github.com/danthedeckie/simpleeval)
library, hardened twice: (1) a whitelist walk over the Python AST accepts only the grammar above
(no attributes, subscripts, comprehensions, lambdas, strings, `**`, comparisons, keyword/star
arguments, `__import__` …); (2) the evaluator is stripped of every other node type and series are
wrapped in a plain object, so no pandas/numpy method (e.g. `to_csv`, `to_pickle`) is reachable.
`eval`/`exec`/`compile` are never called (a test scans the package source for this).
Injection attempts are covered in `tests/test_derive_parser.py`.

## Kind inference (`indicator_type` of the result)
Rules per operator (events behave as counts; the kind of a pure shift `lag` is preserved):

| expression | result kind |
|---|---|
| count + count, `sum(count…)`, count × positive constant, `rolling_sum/mean(count)`, `abs(count)` | `count` |
| count / count, `rolling_ratio(count, count, n)` | `proportion` (scale 1) – **if values stay in [0, 1]**, else `rate` |
| `100 * count / count` (or `count * 100 / count`) | `proportion`, `value_scale` 100 – if values stay in [0, 100], else `rate` |
| proportion × 100 / `pc / 100` | proportion rescaled (1 ↔ 100); any other constant ⇒ `rate` |
| proportion × count | `count` (expected count) |
| proportion × proportion | `proportion` |
| count − count, `diff`, `pct_change`, unary minus | `rate` |
| proportion ± proportion, proportion / proportion, anything / proportion, count / constant, mixed kinds, `sqrt`, `log1p` | `rate` |
| event + event | `event` |

The inferred kind and the step-by-step trail are stored in `lineage.kind_inference`. Passing
`indicator_type` is an *assertion*: if it differs from the inference the request fails (422).
A proportion that leaves `[0, value_scale]` is downgraded to `rate` with a warning (never clipped).

## Alignment and missing values
* Sources must be weekly and on the same weekday grid (dates differing by non-multiples of 7 days ⇒
  `alignment_error`) and share `season_length`.
* `align: intersection` (default) — result range = overlap of all sources; `union` — earliest start
  to latest end, weeks outside a source's range are missing.
* **Missing is never silently filled with 0.** A missing source value (NULL, absent week, outside
  its range) gives a missing result; `x / 0` is undefined ⇒ missing (never ∞); rolling windows with
  any missing week are missing; `sum()` is missing if any term is. Leading/trailing missing weeks of
  the result (e.g. from `lag`) are trimmed and reported; internal gaps stay NULL.
* The alignment report (`lineage.alignment`) lists grid, per-source missing/outside counts and the
  number of missing result weeks.

## Lineage and persistence
`series.derived_expr`, `derived_from` (sorted source ids), `bindings` (aliases) and `lineage`
(`expr`, `bindings`, `sources[]` with kind/scale/source_id/16-hex data fingerprint, `alignment`,
`kind_inference`, ailab version, `recomputed_at`) are stored with the series in DuckDB; edges are
also in table `series_lineage`. Licence/attribution/notice of all sources are concatenated into the
derived series. `GET /api/v1/series/{id}/lineage` also lists dependents.

* The result is a **snapshot**: `ailab derive recompute ID` / `POST /series/{id}/recompute`
  re-evaluates against current sources.
* Deleting a source that has dependents is refused (409 `has_dependents`) unless `cascade=true`.
* A derived series cannot be its own source or create a cycle; a base series cannot be overwritten
  by a derivation.
* Derived series are analysed like any other (quality, backtest, policy, alerts, export); the static
  export lists `derived_from` and the sources of all ancestors in `sources.json`.
