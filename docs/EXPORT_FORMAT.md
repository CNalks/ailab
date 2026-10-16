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
series[]: {series_id, name, path, bytes, gzip_bytes, sha256, data_through, indicator_type, source_id,
policy:{forecast_allowed, reason, display_mode, model_family, horizon_weeks}, quality_grade,
latest_alert_signal:{<method>: bool}, download}, sources: "sources.json"`

## series/<id>.json
| key | content |
|---|---|
| `series` | metadata: `series_id, name, disease, geography, unit, indicator_type (count/proportion/rate/event), season_length, source_id, licence, attribution, notice, retrieved_at, sha256, derived_expr, derived_from` |
| `data_through` | last week-start date (Sunday), ISO |
| `quality` | `grade A/B/C`, `n_weeks, n_missing, data_from, data_through`, `checks[] {check, ok, severity, detail}`, `reasons[]` |
| `model_input` | `interpolated_points` (gaps ≤ 2 weeks interpolated for fitting only; never shown as observed) |
| `policy` | `forecast_allowed, reason, display_mode (forecast_band / forecast_point_only / monitor_only / history_only), model_family, horizon_weeks, indicator_type, warnings[]` |
| `backtest` | `null` if not possible; else `n_windows, horizon, step_weeks, first_origin, last_origin, baseline, chosen_model, models{name:{mae, mase, rel_mae_vs_seasonal_naive, mae_by_horizon{h}}}` |
| `forecast` | `null` unless `policy.forecast_allowed`; else `origin, model, interval_method, calibration{80,95:{nominal, empirical_loo, n_windows}}, points[] {h, ds, point, lo80, hi80, lo95, hi95}` (bands absent if `display_mode=forecast_point_only`) |
| `alerts[]` | `rule_id, method, baseline_weeks, n_evaluated, n_signals, latest, latest_is_signal, recent[] {ds, observed, expected, score, threshold, alarm}, signal_periods[], wording` |
| `observations[]` | `{ds, y}` on the regular weekly grid; `y: null` = missing (never 0) |

Rendering rules for the site: show `data_through` and "precomputed, not live"; draw the forecast
band only if `display_mode == "forecast_band"`; show `policy.reason` when no forecast; always show
`series.attribution` and `series.notice`; call alerts "statistical signals", never "outbreak";
do not use red/amber/green for them.
