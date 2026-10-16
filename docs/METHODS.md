# Methods (v0.3)

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
**Farrington-flexible style** (v0.3, `ailab.alerts.farrington_scores`): for each week a quasi-Poisson GLM
(`log μ = a + b·t`, dispersion φ from Pearson χ², floored at 1) is fitted on the same weeks ± 3 of up to the 5
previous years; past outbreak weeks are down-weighted by a robust (MAD) re-weighting of the residuals; the 2/3-power
transformed prediction limit gives the score; threshold 2 (benchmarked: ≈ 1 false alarm / 100 clean weeks).
It is a simplified re-implementation (no R reference output comparison).

**Quality gate** (v0.3, `ailab.gate`, [MODEL_POLICY.md](MODEL_POLICY.md#quality-gate-v03)). Data checks on the
fitting frame: length in seasons, missing share / longest gap / trailing gap, constant and zero-inflated series
(ADI, CV²), STL seasonal strength `F_S = max(0, 1 − Var(R)/Var(S+R))` (Hyndman), stationarity of the
STL-deseasonalised series (ADF + KPSS + drift; warning only), level-shift scan (OLS of the first difference on a
step dummy, detrended, HAC t statistic), variance stability (last season vs. earlier, Levene), Hampel outliers,
reporting-delay tail (year-over-year log drop; or the explicit `reporting_lag_weeks`). Backtest checks: MAE ratio
vs. seasonal-naive, number of leading horizons that beat it, origins/seasons covered, leave-one-window-out 80 %
coverage. The decision is `allowed`, `limited` (shorter horizon, wider intervals ×1.25–1.5, band withheld, baseline
model) or `refused`, with machine-readable reason codes and en/zh/fr messages.

## Libraries reused vs. custom code
| need | reused | custom (why) |
|---|---|---|
| forecasting models | `statsforecast` (SeasonalNaive, Naive, MSTL+ETS, AutoTheta, cross-validation) | – |
| prediction intervals | quantile of the already computed backtest errors (numpy) | ~30 lines of split-conformal: reuses the backtest (no extra refits, statsforecast's `ConformalIntervals` would refit again), per horizon, in transformed space, plus leave-one-out coverage |
| derived-series evaluation | `simpleeval` (evaluator) + pandas/numpy ops | whitelist AST walk, typed series wrapper (kind inference, missing semantics) |
| storage | DuckDB, Parquet via pandas/pyarrow | – |
| API / CLI | FastAPI, pydantic, uvicorn, typer | thin ASGI middleware (request id, body-size limit) |
| aberration detection | `statsmodels` GLM (Farrington) | EARS C1–C3 and CUSUM are ~60 lines of formulas. No light, maintained pure-Python package exists (the maintained options, R `surveillance` / `epysurv`, need an R runtime); Farrington-flexible-style scoring is ~80 lines on top of the GLM |
| quality gate | `statsmodels` (STL, `adfuller`, `kpss`, OLS + HAC), `scipy` (Levene) | thresholds, decision logic, Hampel outliers, tail heuristic (~1.3 k lines incl. messages); `ruptures` was **not** added (+ heavy deps for a task a detrended HAC step scan solves well enough here) |
