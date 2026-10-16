# Methods (v0.1)

**Grid.** Weekly series, week start = Sunday (MMWR). Missing weeks are NULL; ≤ 2 consecutive gaps are
linearly interpolated *for model fitting only*; longer gaps block forecasting.

**Models** (statsforecast, CPU): SeasonalNaive (baseline, season 52), Naive, MSTL with ETS trend,
AutoTheta. Plain seasonal ETS is not offered at weekly period 52.

**Backtest.** Rolling origin: default 26 origins, 2 weeks apart, horizon 8; each origin refits on data
up to the origin only. Metrics: MAE, MASE (scaled by in-sample seasonal-naive error before the first
origin), MAE ratio to baseline, MAE per horizon.

**Selection & policy.** Best candidate by overall MAE; allowed only if it beats the baseline at
horizon 1 and then for each consecutive horizon (`horizon_weeks`). Rules for short/sparse/event/gappy
series are in `ailab.policy`.

**Intervals.** Per-horizon empirical quantile of absolute backtest errors, level (n+1)·p/n
(finite-sample), symmetric around the point forecast, clipped to ≥ 0 (and ≤ 100 for proportions).
Reported coverage is leave-one-window-out. Caveat: errors mix seasons (peaks and troughs), so bands are
marginal, not conditional on season phase.

**Alerts.** EARS C1/C2/C3 on a 7-week baseline (guard 0/2/2 weeks); CUSUM on baseline z-scores
(k = 0.5, h = 4, restart after signal). They do not model seasonality; a normal seasonal rise signals.
