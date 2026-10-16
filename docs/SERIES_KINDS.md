# Series kinds (`indicator_type`)

Every series has an explicit kind. It is **required** when creating a series through the API/CLI
and is stored in the series metadata. The same engine handles all kinds; the kind selects the
transform, the allowed aggregation and how strict the forecast policy is.

| kind | meaning | stored values | model transform | forecast / interval range | aggregation | forecast policy |
|---|---|---|---|---|---|---|
| `count` | tallies per week | ≥ 0 (validated on write) | `log1p` (zeros fine) | ≥ 0, intervals asymmetric (longer upper tail) | sum | beat seasonal naive at h=1 |
| `proportion` | part / whole | within `[0, value_scale]`, `value_scale` = 1 (fraction) or 100 (percent) | `logit` after clipping to `[1e-4, 1-1e-4]` | always inside `[0, value_scale]` | **ratio of sums** (never a plain mean) | **stricter**, see below |
| `rate` | generic non-negative measure (per-exposure rates, differences, scaled counts); may be negative if produced by `-`/`diff` | any finite value | `log1p` | ≥ 0 | sum / ratio of sums | like `count` (negative values ⇒ not forecast) |
| `event` | sparse notifiable events | ≥ 0 | – | – | sum | **monitor only**, never forecast; alerts still run |

## Transforms and intervals
Models (SeasonalNaive baseline, Naive, MSTL+ETS, AutoTheta from `statsforecast`) are fitted on the
transformed series. The rolling-origin backtest back-transforms predictions, so **MAE / MASE and the
comparison with the seasonal-naive baseline are in original units**. Prediction intervals are the
empirical quantiles of the absolute backtest errors *in transformed space* (split-conformal,
per horizon); the interval ends are then back-transformed, which keeps counts ≥ 0 and proportions
in `[0, value_scale]` by construction (no after-the-fact clipping of a symmetric band).

## Proportion policy (stricter)
A proportion is forecast only if all hold (`ailab.policy.KIND_RULES`):

1. the usual quality gate (≥ 3 seasons of history, gaps ≤ 2 weeks, mostly non-zero);
2. the best model beats seasonal naive at horizon 1 **and** over ≥ 2 leading horizons;
3. its overall backtest MAE is ≤ 95 % of the baseline's (a margin, not a tie);
4. the backtest spans ≥ 3 seasons (default backtest design, see METHODS.md);
5. the 80 % leave-one-out coverage is ≥ 70 % (otherwise the proportion is **not forecast**; for
   counts the band is merely withheld at < 60 %).

Reasons are machine-readable in `policy.reason`: `no_skill_vs_seasonal_naive`,
`skill_horizon_too_short`, `backtest_lt_3_seasons`, `band_undercoverage_in_backtest`, …
`ailab.policy.decide(..., proportion_forecast=False)` switches proportion forecasts off entirely.

## Aggregation rule
Aggregating proportions across weeks or series must be a **ratio of sums**
(`sum(positives…) / sum(tests…)`, `rolling_ratio(pos, tests, 4)`); weeks with few tests must not
weigh as much as weeks with many. The derived-series language therefore rejects `rolling_mean`,
`rolling_sum` and `sum` of a proportion with an explanatory error.
