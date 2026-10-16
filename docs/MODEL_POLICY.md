# Model policy

Everything the service says about the future is gated by a **policy**: a small, machine-readable
verdict returned with every forecast (`GET /api/v1/series/{id}/forecast` and `/policy`) and stored
in every static export. The policy exists to make "no forecast" a normal, explained outcome.

## Policy fields

```json
{"forecast_allowed": true,
 "reason": "beats_seasonal_naive_in_rolling_origin_backtest",
 "display_mode": "forecast_band",
 "model_family": "mstl_ets",
 "horizon_weeks": 8,
 "indicator_type": "proportion",
 "warnings": []}
```

| field | values | meaning |
|---|---|---|
| `forecast_allowed` | bool | `false` ⇒ the response contains **no forecast values**; `forecast` is `null` |
| `reason` | string | why (see table below); when allowed it names the criterion that was met |
| `display_mode` | `forecast_band`, `forecast_point_only`, `monitor_only`, `history_only` | how a client should render: band = point + 80 % and 95 % intervals; point-only = the band was withheld because its leave-one-window-out 80 % coverage was below the kind's minimum (60 % counts/rates); `monitor_only` = show history and alerts only; `history_only` = data problems, show the history with the reason |
| `model_family` | `mstl_ets`, `theta`, `naive`, `seasonal_naive`, `none` | the model that won the backtest (`none` when not allowed) |
| `horizon_weeks` | int | number of leading weeks for which the chosen model beat the seasonal-naive baseline in the backtest, capped at the requested horizon; 0 when not allowed |
| `indicator_type` | `count`, `proportion`, `rate`, `event` | kind whose rules were applied |
| `warnings` | list of strings | non-fatal caveats, e.g. backtest spans < 3 seasons, horizon truncated, 80 % band under-covers, quality grade B, history capped at 840 weeks |

## Decision order (first match wins)

| # | condition | `reason` | `display_mode` |
|---|---|---|---|
| 1 | kind `event` | `indicator_type_event_sparse` | `monitor_only` |
| 2 | quality check `length` fails (< 3 seasons of weeks) | `history_lt_3_seasons` | `monitor_only` |
| 3 | < 50 % of observed weeks are > 0 | `sparse_series` | `monitor_only` |
| 4 | longest gap > 2 weeks | `data_gaps_too_long` | `history_only` |
| 5 | negative values | `negative_values` | `history_only` |
| 6 | backtest impossible | `backtest_not_possible` | `monitor_only` |
| 7 | best candidate (lowest backtest MAE of MSTL-ETS / AutoTheta / Naive) does not beat seasonal-naive at horizon 1, or its overall MAE ≥ the kind's limit × baseline | `no_skill_vs_seasonal_naive` | `monitor_only` |
| 8 | proportions: beats the baseline for fewer than 2 leading horizons | `skill_horizon_too_short` | `monitor_only` |
| 9 | proportions: backtest spans < 3 seasons | `backtest_lt_3_seasons` | `monitor_only` |
| 10 | proportions: 80 % band under-covers (< 70 %) | `band_undercoverage_in_backtest` | `monitor_only` |
| 11 | otherwise | `beats_seasonal_naive_in_rolling_origin_backtest` | `forecast_band` (or `forecast_point_only`) |

Kind rules (`ailab.policy.KIND_RULES`): counts and rates need to beat the baseline at horizon 1
(overall MAE ratio < 1.0); proportions need ≥ 2 leading horizons, MAE ratio < 0.95, a backtest of
≥ 3 seasons and 80 % coverage ≥ 70 %.

## Data-quality grade

`GET /api/v1/series/{id}/quality` returns a checklist and a grade; the policy reads the blocking
checks from it.

| check | passes when | severity |
|---|---|---|
| `length` | ≥ 3 seasons of weekly points (`n_weeks / season_length`) | **fail** |
| `completeness` | no missing weeks | warn |
| `gaps` | longest run of missing weeks ≤ 2 | **fail** |
| `non_zero_share` | ≥ 50 % of observed weeks are > 0 | **fail** |
| `zero_runs` | no run of ≥ 8 exact zeros in an otherwise non-sparse series (possible implicit zero-fill) | warn |
| `negative_values` | no negative values | **fail** |

Grade **C** = any *fail*, **B** = only warnings, **A** = all passed. Gaps ≤ 2 weeks are linearly
interpolated for model fitting only (never shown as observed).

## How the backtest works

* Models: SeasonalNaive (the baseline to beat), Naive, MSTL with ETS trend, AutoTheta
  ([statsforecast](https://github.com/Nixtla/statsforecast)), fitted in the kind's transform
  (`log1p` / `logit`).
* Rolling origin: by default `ceil(3 × season_length / 3)` origins every 3 weeks, horizon = requested
  horizon; each origin refits on earlier data only. `backtest.seasons_covered` states the real span.
* Intervals: per-horizon empirical quantiles of absolute backtest errors in the transformed space
  (split-conformal style), mapped back; leave-one-window-out coverage is reported in `forecast.calibration`.
* Only the most recent **840 weeks** are used for fitting (cost grows roughly quadratically with
  length; see [TEST_REPORT.md](TEST_REPORT.md)).

**Caveats** (also in the README): the winning model is selected on the same backtest that is
reported; windows overlap; final (revised) data are used rather than real-time data; intervals are
marginal over seasons. A passed policy means "beat a naive baseline in hindsight on this series",
nothing more.

## Alert signals

Alerts are *signals*, not declarations. For week *t* the baseline is the preceding `baseline` weeks
(default 7; 3–104), after a guard band of 0 weeks (C1) or 2 weeks (C2, C3, CUSUM); the baseline needs
at least `max(3, 0.7 × baseline)` observed weeks. The z-score is `(y_t − mean) / sd`, with
`sd = max(sd, 1e-6, 5 % of |mean|)` so flat baselines do not give infinite scores.

| method | score | default threshold |
|---|---|---|
| `ears_c1` | z (no guard band) | 3 |
| `ears_c2` | z (2-week guard band) | 3 |
| `ears_c3` | sum over the last 3 weeks of `max(0, z − 1)` | 2 |
| `cusum` | `S_t = max(0, S_{t−1} + z_t − 0.5)`, restarts at 0 after a signal | 4 |

A stored rule (`PUT /api/v1/alerts/rules`) fixes series, method, baseline and optional threshold;
`GET /api/v1/alerts/evaluate` runs all enabled rules. **Seasonality is not modelled**: the normal
winter rise signals. Output wording is "statistical signal"; there are no risk colours.
