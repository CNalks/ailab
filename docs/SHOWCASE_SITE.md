# Showcase site (`site/`)

A minimal, trilingual (zh default, en, fr) **static** site that presents ailab with five real, precomputed series.
Nothing runs live; the only runtime request is a small status badge that reads `GET /ailab-api/health` and falls back to
"offline · static showcase" on any failure.

| | |
|---|---|
| URL | `https://www.cnalks.cn/ai4health/lab/` (zh), `/en/`, `/fr/` |
| Stack | Astro 5 static build, shared tokens `github:CNalks/design-tokens#v0.1.0` (tokens.css, motion.css, reveal.ts), light default + light/dark toggle (shared `cnalks-theme` key with cnalks.cn) |
| Charts | build-time inline SVG (no chart library, 0 KB of chart JS), CSS variables for both themes, a data table per series as the text alternative |
| Data | `site/public/data/` written by `scripts/build_showcase_data.py` with the real pipeline (`ailab.pipeline.analyse` + `ailab.export.export_site`), format: [EXPORT_FORMAT.md](EXPORT_FORMAT.md) |

## The five series
| id | source | licence | gate (v0.3.0, data through) |
|---|---|---|---|
| `us_ilinet_wili` | CDC FluView (bundled fixture) | public domain (US Gov), attribute CDC | allowed (2026-09-27) |
| `us_ilinet_ili_visits` | CDC FluView (bundled fixture) | same | allowed, `trend_nonstationary` (hard to judge) |
| `england_flu_positivity` | UKHSA dashboard | OGL v3.0 | limited: `outlier_contamination`, `zero_runs` |
| `eu_eea_flu_positivity` | ECDC ERVISS | CC BY 4.0 / ECDC dataset sentence | refused: `insufficient_backtest`, `outlier_contamination` |
| `malta_flu_detections` | ECDC ERVISS | same | refused (deliberately low quality, real): `too_short`, `too_many_gaps` |

National or supra-national aggregates only; no sub-national or community-level data.

## Forecast walkthrough (home page, right after the hero)
"Run a forecast" (`site/src/components/SequenceWorkflow.astro`) walks the visitor through four steps, one at a time:

1. **Choose the series**: one radio row per real series (name, geography, weekly, length, publisher). No outcome is shown here.
2. **Data overview**: time span, frequency, length (≈ seasons), latest value, last-52-week range, missing weeks and longest gap,
   seasonality (`gate.metrics.seasonal_strength` against `thresholds.min_seasonal_strength`) and the history chart; then the
   data check-up findings in plain words (the data-stage `gate.reasons`).
3. **Choose a method**: one card per model that went through the rolling-origin backtest (`backtest.models`: Naive, MSTL,
   AutoTheta and the SeasonalNaive baseline), with the mean absolute error, a bar, and the error relative to the baseline.
   The **recommended** card is `backtest.chosen_model`; `workflow.ts` fails the build unless it is the lowest-MAE candidate
   and equals `forecast.model`. It is pre-selected with a one-line reason. Limited series state the limits; refused series
   explain why nothing is recommended (thin backtest) or why there are no methods (data check blocked).
4. **Result**: the decision badge (allowed / limited / refused), the forecast band, the first and last forecast week with
   the 80% band, the outcome line, and the collapsed **Basis and limits** (the four recorded steps). The static site only
   publishes the forecast of the recommended method: picking another method shows its backtest score and a note, with a
   button to switch back. Showing every method's own forecast would need the export to write per-model `forecast` points
   and interval widths (`interval_widths(bt, model)` per candidate).

A stepper and Back buttons move between steps; choosing another series resets to step 1 and the recommended method.
Without JavaScript every series is listed with its overview, methods and result (step 1 and the buttons are hidden).
Reproduce (CLI / `jq`) is secondary. `npm test` checks fidelity to the JSON, i18n, and the ≤ +15 KB HTML budget vs main.
`SeriesChart` styles are global (prefixed `.chart`) so SVG nodes carry no `data-astro-cid` attribute.


## Refresh data
```bash
# 1. update the snapshots in site/data-src/ (see provenance.json for the exact request URLs and filters)
# 2. recompute
uv run python scripts/build_showcase_data.py      # -> site/public/data (fails if the size budget is exceeded)
# 3. build
cd site && npm ci && npm run build && npm test   # -> site/dist + walkthrough / i18n / size checks
```

## Deploy (static files only)
`site/dist/` is the complete artefact for `/var/www/ai4health/lab/`; nginx needs `location ^~ /ai4health/lab/ { root /var/www; ... }`
(HTML `no-cache`, `/ai4health/lab/_astro/` immutable, `/ai4health/lab/data/` 10 min). Deploy as for the other static sites:
tar over ssh into a `.new` directory, `chown -R www-data`, keep the previous directory as a backup, atomic swap, `nginx -t && reload`.
The badge expects `location = /ailab-api/health` proxying to the ailab service (GET/HEAD only).
