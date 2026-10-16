# Model policy

Everything the service says about the future is gated by a **quality gate** (v0.3, below) and a
**policy**: a small, machine-readable
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
 "warnings": [],
 "gate_decision": "allowed",
 "reason_codes": [],
 "limited": false}
```

| field | values | meaning |
|---|---|---|
| `forecast_allowed` | bool | `false` ⇒ the response contains **no forecast values**; `forecast` is `null` |
| `reason` | string | why (see table below); when allowed it names the criterion that was met |
| `display_mode` | `forecast_band`, `forecast_point_only`, `monitor_only`, `history_only` | how a client should render: band = point + 80 % and 95 % intervals; point-only = the band was withheld because its leave-one-window-out 80 % coverage was below the kind's minimum (60 % counts/rates); `monitor_only` = show history and alerts only; `history_only` = data problems, show the history with the reason |
| `model_family` | `mstl_ets`, `theta`, `naive`, `seasonal_naive`, `none` | the model that won the backtest (`none` when not allowed) |
| `horizon_weeks` | int | number of leading weeks for which the chosen model beat the seasonal-naive baseline in the backtest, capped at the requested horizon; 0 when not allowed |
| `indicator_type` | `count`, `proportion`, `rate`, `event` | kind whose rules were applied |
| `gate_decision` | `allowed`, `limited`, `refused` | outcome of the quality gate (v0.3, additive) |
| `reason_codes` | list of strings | machine-readable gate reason codes, most severe first (see [reason codes](#reason-codes)) |
| `limited` | bool | `true` ⇒ forecast allowed but constrained (shorter horizon / wider intervals / baseline model) |
| `warnings` | list of strings | non-fatal caveats, e.g. backtest spans < 3 seasons, horizon truncated, 80 % band under-covers, quality grade B, history capped at 840 weeks |

## Quality gate (v0.3)

The gate answers *"may this series be forecast at all?"* before any number is shown, and says **why
not** in a form both programs and people can use. It is transparent (every threshold and measured
value is in the response), configurable (per kind, per frequency, per series) and conservative in
the right direction: a missing forecast costs an analyst a look, a bad forecast costs trust.

* Stage 1 — **data checks** (cheap, no model fitting; `GET /series/{id}/gate`, `ailab gate --quick`,
  and the `gate` block of `/quality`).
* Stage 2 — **backtest checks** (rolling-origin skill versus the seasonal-naive baseline, interval
  coverage), run by `/forecast`, `/policy` and `ailab gate`.

The gate overlays the original policy rules (decision order below is unchanged and still produces the
legacy `reason` strings), so clients written for v0.2 keep working.

### Decision

```json
{"decision": "refused",           // allowed | limited | refused
 "forecast_allowed": false,       // limited counts as allowed
 "limited": false, "confidence": "high", "hard_to_judge": false,
 "reasons": [{"code": "too_short", "severity": "block",
              "message": {"en": "...", "zh": "...", "fr": "..."},
              "metrics": {...}, "thresholds": {...}}],
 "reason_codes": ["too_short"],
 "limits": {"max_horizon_weeks": null, "interval_inflation": 1.0, "baseline_only": false, "point_only": false},
 "recommended_action": {"code": "collect_more_history", "message": {"en": "...", "zh": "...", "fr": "..."}},
 "metrics": {...}, "thresholds": {...}, "threshold_sources": {...}, "skipped_checks": {...}}
```

Severity: **block** ⇒ `refused` (no forecast values, `forecast: null`); **limit** ⇒ `limited` (forecast
with constraints); **warn** ⇒ forecast unchanged, caveat attached to `policy.warnings`. `confidence`
is `high` / `medium` / `low` from the number and severity of the findings. `hard_to_judge` is set for
non-stationary / unstable series (see below).

### Reason codes

| code | severity | triggered when (default thresholds, weekly) |
|---|---|---|
| `kind_event_not_forecast` | block | `indicator_type=event` |
| `negative_values` | block | any value < 0 |
| `too_short` | block | < 3 seasons of periods (`min_seasons_block`) |
| `short_history` | warn | < 4 seasons (`min_seasons_warn`) |
| `too_many_gaps` | block / limit | missing share > 25 % / > 10 %, or an internal gap > 2 periods (`max_missing_frac_block/_limit`, `max_gap`) |
| `long_gap_at_end` | block / warn | the last > 2 periods are missing (`max_trailing_gap`) / 1–2 missing |
| `stale_data` | block / warn | latest data older than 26 / 4 periods vs. `as_of` (evaluated only with `as_of` or `AILAB_GATE_STALE_CHECK=1`) |
| `constant_series` | block | sd / mean < 1e-3 or a single distinct value |
| `mostly_zeros` / `mostly_zeros_warn` | block / warn | share of observed periods > 0 below 50 % / 80 % (ADI and CV² reported) |
| `zero_runs` | warn | ≥ 8 consecutive zeros inside an active series (implicit zero-fill?) |
| `no_seasonal_signal_but_seasonal_expected` | warn | STL seasonal strength F_S < 0.35 although `season_length ≥ 4` |
| `trend_nonstationary` | warn (`hard_to_judge`) | after STL deseasonalising, ADF does not reject a unit root and KPSS rejects stationarity (or drift ≥ 0.5 model units) |
| `structural_break` | limit / warn | detrended level-shift scan: HAC t of the step ≥ 8 and shift ≥ 0.5 model units (≈ 65 %); *limit* if inside the last 2 seasons, else *warn* |
| `unstable_variance` | warn / limit | residual spread of the last season ≥ 2.5× / 5× earlier (plus Levene test) |
| `outlier_contamination` | warn / limit | Hampel outliers (|dev| > 6 robust SD and ≥ 0.5 model units) > 2 % / 6 % of periods |
| `reporting_delay_tail_incomplete` | limit / warn | explicit `reporting_lag_weeks`: trailing weeks are excluded from fitting (limit); otherwise a heuristic flags a recent drop far below the year-over-year expectation (warn; low power) |
| `insufficient_backtest` | block / limit | fewer than 5 / 10 backtest origins |
| `short_backtest_span` | warn | backtest spans < 3 seasons |
| `backtest_worse_than_baseline` | block | best candidate's MAE ≥ 1.0 × seasonal-naive (proportions: 0.95) |
| `marginal_skill` | limit | MAE ratio ≥ 0.95 (but < 1.0): shorter horizon, wider intervals |
| `skill_horizon_too_short` | block | beats the baseline for fewer leading horizons than required (proportions: 2) |
| `no_skill_beyond_persistence` | warn / limit | the winner is plain Naive (no learnable structure) |
| `interval_undercoverage` | block / limit | leave-one-window-out 80 % coverage below `cov80_block` (proportions: 0.70) / `cov80_limit` 0.6 (band withheld) |

Checks that cannot run (e.g. STL on < 2 seasons, stale check without `as_of`) are listed in `skipped_checks` with the reason, never silently passed.

(`kind_event_not_forecast` … `skill_horizon_too_short` map onto the legacy `policy.reason` strings listed in the
decision-order table below; the gate adds the checks the legacy rules did not have.)

**Stationarity is deliberately a warning.** Raw ADF/KPSS flag real influenza series (strong
seasonality, growing participation) as non-stationary, and refusing seasonal epidemics would be a
false rejection. The gate therefore (1) removes the seasonal component with STL, (2) tests the
remainder with ADF + KPSS and a drift measure, (3) only warns (`trend_nonstationary`,
`hard_to_judge=true`, action `interpret_with_care`), and (4) lets the **backtest decide**: a series is
refused only when no candidate beats the seasonal-naive baseline. Limits: ADF/KPSS have low power on
~8 seasons of weekly data; the break scan is a heuristic (sup-t on a detrended step dummy), not a
formal Andrews test; STL needs ≥ 2 seasons. Validation numbers: [BENCHMARK.md](BENCHMARK.md).

### Limited mode

Instead of refusing, a *limit* finding constrains the forecast: horizon capped at 4 weeks
(`limited_max_horizon`), intervals widened ×1.25 (×1.5 for a recent structural break), the band withheld
(`point_only`), or a baseline model (Naive / SeasonalNaive, whichever won the backtest) used. The
applied constraints are in `gate.limits`, mirrored in `policy.warnings`, and `policy.limited=true`.

### HTTP semantics

A refusal is **HTTP 200** with `policy.forecast_allowed=false`, `forecast: null` and the gate
block: the request was valid and the service answered "no forecast, because …". 422 stays reserved for
invalid requests (bad horizon, bad `as_of`, bad gate overrides) so existing clients that treat non-2xx as
an error are not broken by data-quality outcomes.

### Configuration (layers, later wins)

1. built-in defaults → per kind (`proportion` is stricter: MAE ratio < 0.95, ≥ 2 leading horizons, 80 % coverage ≥ 0.70, backtest ≥ 3 seasons) → per frequency (`W`, `M`, `D` tables in `ailab.gate`; the service is weekly today);
2. environment: `AILAB_GATE_<THRESHOLD>=value` (all series, e.g. `AILAB_GATE_MIN_SEASONS_BLOCK=4`) or
   `AILAB_GATE_JSON='{"all":{...},"kinds":{"count":{...}},"series":{"my_id":{...}}}'`; a malformed value stops the
   service at start-up; `AILAB_GATE_STALE_CHECK=1` judges staleness against today's date;
3. per series: `meta.gate = {"min_seasons_block": 2.5, ...}` (`ailab series add --gate KEY=VALUE`), plus
   `meta.mask_periods = [["2020-03-15","2021-09-26"]]` to exclude disruption periods from fitting, backtest
   scoring and interval calibration (opt-in; neutral-to-worse on real FluView, see BENCHMARK.md).

`gate.threshold_sources` shows which layer set each overridden value. The full list of thresholds with defaults
is the `Thresholds` dataclass in `src/ailab/gate.py` (also echoed in every response under `thresholds`).
Unknown names or wrongly typed values are rejected (422 on create).

CLI: `ailab gate <series> [--h 8] [--lang en|zh|fr] [--as-of YYYY-MM-DD|today] [--quick] [--set KEY=VALUE] [--json]`
(exit code 1 when refused).

### Alerts

Alerts have their own gate (`ailab.gate.assess_alert`): refused when the history cannot support the baseline
(`alert_baseline_too_short`), the baseline window has too few observed weeks (`alert_baseline_gappy`), or the method does not
apply (`alert_method_not_applicable`, Farrington on proportions, `alert_not_enough_seasons`); degraded
(`status: "degraded"`, low confidence, signals still shown) for short history, near-constant baselines, low
counts or a contaminated baseline. A refused scan returns no signals (`n_signals_suppressed`).

## Decision order (first match wins)

Legacy rules, still evaluated first; the gate can only make the outcome stricter (never allow what a rule denied).

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
| `farrington` | Farrington-flexible-style: quasi-Poisson GLM with trend on the same weeks (± 3) of up to 5 earlier years, robust re-weighting of past outbreaks, 2/3-power-transformed upper prediction limit; score = standardised exceedance | 2 |
| `cusum` | `S_t = max(0, S_{t−1} + z_t − 0.5)`, restarts at 0 after a signal | 4 |

A stored rule (`PUT /api/v1/alerts/rules`) fixes series, method, baseline and optional threshold;
`GET /api/v1/alerts/evaluate` runs all enabled rules. **EARS and CUSUM do not model seasonality**: the normal winter rise signals; `farrington` does (needs ≥ 3 seasons of counts) and is added by default for seasonal count/rate series (≈ 1 vs. 8 false alarms per 100 clean weeks, [BENCHMARK.md](BENCHMARK.md)). Output wording is "statistical signal"; there are no risk colours.
