# Methods (v0.2)

**Grid.** Weekly series, week start = Sunday (MMWR). Missing weeks are NULL; ≤ 2 consecutive gaps are
linearly interpolated *for model fitting only*; longer gaps block forecasting.

**Models** (statsforecast, CPU): SeasonalNaive (baseline, season 52), Naive, MSTL with ETS trend,
AutoTheta. Plain seasonal ETS is not offered at weekly period 52.

**Transforms.** Fitting happens in a kind-specific space (`count/rate`: log1p; `proportion`: logit on
the 0..1 scale, clipped to [1e-4, 1-1e-4]); see [SERIES_KINDS.md](SERIES_KINDS.md). Metrics are
reported in original units.

**Backtest.** Rolling origin, default design **spans ≥ 3 seasons**: `ceil(3 × season_length / step)`
origins, `step` = 3 weeks (52 origins for weekly data), horizon 8; each origin refits on data
up to the origin only. `backtest.seasons_covered` reports the achieved span (series too short to
span 3 seasons get a warning; proportions are denied). Metrics: MAE, MASE (scaled by in-sample seasonal-naive error before the first
origin), MAE ratio to baseline, MAE per horizon.

**Selection & policy.** Best candidate by overall MAE; allowed only if it beats the baseline at
horizon 1 and then for each consecutive horizon (`horizon_weeks`). Rules for short/sparse/event/gappy
series are in `ailab.policy`.

**Intervals.** Per-horizon empirical quantile of absolute backtest errors *in transformed space*,
level (n+1)·p/n (finite-sample), symmetric there and back-transformed (so ≥ 0 for counts and inside
[0, scale] for proportions, asymmetric on the original scale).
Reported coverage is leave-one-window-out. Caveat: errors mix seasons (peaks and troughs), so bands are
marginal, not conditional on season phase.

**Alerts.** EARS C1/C2/C3 on a 7-week baseline (guard 0/2/2 weeks); CUSUM on baseline z-scores
(k = 0.5, h = 4, restart after signal). They do not model seasonality; a normal seasonal rise signals.

## Libraries reused vs. custom code
| need | reused | custom (why) |
|---|---|---|
| forecasting models | `statsforecast` (SeasonalNaive, Naive, MSTL+ETS, AutoTheta, cross-validation) | – |
| prediction intervals | quantile of the already computed backtest errors (numpy) | ~30 lines of split-conformal: reuses the backtest (no extra refits, statsforecast's `ConformalIntervals` would refit again), per horizon, in transformed space, plus leave-one-out coverage |
| derived-series evaluation | `simpleeval` (evaluator) + pandas/numpy ops | whitelist AST walk, typed series wrapper (kind inference, missing semantics) |
| storage | DuckDB, Parquet via pandas/pyarrow | – |
| API / CLI | FastAPI, pydantic, uvicorn, typer | thin ASGI middleware (request id, body-size limit) |
| aberration detection | – | EARS C1–C3 and CUSUM are ~60 lines of formulas. No light, maintained pure-Python package exists (the maintained options, R `surveillance` / `epysurv`, need an R runtime); Farrington is planned via golden files generated once with R |
