# Export format (schema_version "1")

Produced by `ailab demo` / `ailab export` / `POST /api/v1/export`. Everything is static, precomputed
and deterministic for the same inputs (apart from `manifest.generated_at`). Consumers (e.g. a static
Astro site) only read these files.

```
out/
  manifest.json            index of series + policy summary + sha256 of each file
  sources.json             licence + attribution + mandatory notice per source
  series/<series_id>.json  full analysis of one series (see below)
  downloads/<series_id>.parquet   columns: series_id, period_start, value  (NULL = missing)
```

Budgets (export fails otherwise): each `series/*.json` ≤ 60 KB gzipped; all JSON ≤ 1.5 MB.

## manifest.json
`schema_version, ailab_version, generated_at (UTC ISO), static_precomputed: true, disclaimer,
series[]: {series_id, name, path, bytes, gzip_bytes, sha256, data_through, indicator_type, derived_from[], source_id,
policy:{forecast_allowed, reason, display_mode, model_family, horizon_weeks}, gate:{decision, confidence, reason_codes}, quality_grade,
latest_alert_signal:{<method>: bool}, download}, sources: "sources.json"`

## series/<id>.json
| key | content |
|---|---|
| `series` | metadata: `series_id, name, disease, geography, unit, indicator_type (count/proportion/rate/event), value_scale (proportion upper bound, 1 or 100), season_length, source_id, licence, attribution, notice, retrieved_at, sha256, derived_expr, derived_from, bindings, lineage (derived series only: sources, alignment report, kind inference), created_at` |
| `data_through` | last week-start date (Sunday), ISO |
| `quality` | `grade A/B/C`, `n_weeks, n_missing, data_from, data_through`, `checks[] {check, ok, severity, detail}`, `reasons[]` |
| `model_input` | `interpolated_points` (gaps ≤ 2 weeks interpolated for fitting only; never shown as observed) |
| `policy` | `forecast_allowed, reason, display_mode (forecast_band / forecast_point_only / monitor_only / history_only), model_family, horizon_weeks, indicator_type, warnings[]` |
| `gate` | (0.3) the full quality-gate decision: `decision allowed/limited/refused, confidence, reasons[] {code, severity, message{en,zh,fr}, metrics, thresholds}, reason_codes[], limits, recommended_action, metrics, thresholds` — see [MODEL_POLICY.md](MODEL_POLICY.md#quality-gate-v03) |
| `backtest` | `null` if not possible; else `n_windows, seasons_covered, transform (log1p|logit), horizon, step_weeks, first_origin, last_origin, baseline, chosen_model, models{name:{mae, mase, rel_mae_vs_seasonal_naive, mae_by_horizon{h}}}` |
| `forecast` | `null` unless `policy.forecast_allowed`; else `origin, model, transform, interval_method, calibration{80,95:{nominal, empirical_loo, n_windows}}, points[] {h, ds, point, lo80, hi80, lo95, hi95}` (bands absent if `display_mode=forecast_point_only`) |
| `alerts[]` | `rule_id, method, baseline_weeks, n_evaluated, n_signals, latest, latest_is_signal, recent[] {ds, observed, expected, score, threshold, alarm}, signal_periods[], wording` |
| `observations[]` | `{ds, y}` on the regular weekly grid; `y: null` = missing (never 0) |

Rendering rules for the site: show `data_through` and "precomputed, not live"; draw the forecast
band only if `display_mode == "forecast_band"`; show `policy.reason` (or the `gate.reasons[].message` in the reader's language) when no forecast; always show
`series.attribution` and `series.notice`; call alerts "statistical signals", never "outbreak";
do not use red/amber/green for them.
