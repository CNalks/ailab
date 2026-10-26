# Showcase site (`site/`)

A minimal, trilingual (zh default, en, fr) **static** site that presents ailab with five real, precomputed series.
Nothing runs live; the only runtime request is a small status badge that reads `GET /ailab-api/health` and falls back to
"offline · static showcase" on any failure.

| | |
|---|---|
| URL | `https://www.cnalks.cn/ai4health/lab/` (zh), `/en/`, `/fr/`; forecast walkthrough at `…/forecast/`, `/en/forecast/`, `/fr/forecast/` |
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

## Pages
* **Home** (`src/views/Lab.astro`), in this order: hero (primary button "Run a forecast" → the forecast page, secondary
  GitHub), three ways to use it, "From a weekly series to a trustworthy signal" (four plain-language steps: tidy the data,
  quality gate, forecast, unusual weeks), methods + Basis and limits, quick start (Docker / Python / API snippets and doc
  links, no buttons), licences and sources. Old in-page anchors of the walkthrough and the removed series cards
  (`#workflow`, `#series`, `#s-<id>`) are sent to the forecast page by a two-line inline script.
* **Forecast** (`src/views/Forecast.astro`): breadcrumb (ailab home › Run a forecast), the walkthrough below with its
  title as `h1`, and a "Back to the home page" link. The header nav links it (marked `aria-current` there).

## Forecast walkthrough (`/forecast/`)
"Run a forecast" (`site/src/components/SequenceWorkflow.astro`) walks the visitor through four steps, one at a time:

1. **Choose the series**: one radio row per real series (name, geography, weekly, length, publisher). No outcome is shown here.
   (The per-series cards that used to follow the walkthrough on the home page are gone; their JSON / Parquet downloads
   are now in step ②.)
2. **Data overview**: time span, frequency, length (≈ seasons), latest value, last-52-week range, missing weeks and longest gap,
   seasonality (`gate.metrics.seasonal_strength` against `thresholds.min_seasonal_strength`) and the history chart; then the
   data check-up findings in plain words (the data-stage `gate.reasons`). If a series has **no selectable method** (all
   red, derived at build time — today EU/EEA and Malta, the refused series), the walkthrough stops here: a red
   conclusion "This series is not suitable for forecasting" with the plain-language reasons from the recorded gate
   metrics (Malta: 51 weeks ≈ 1 season < 3 and 80% missing / 39-week gap; EU/EEA: backtest covers 2.3 seasons < 3),
   the collapsed **Basis and limits**, and only a "Pick another series" button. Steps ③ and ④ are greyed out in the
   stepper and their markup is not rendered for that series.
3. **Choose a method**: every forecasting method the repository has, each with a state derived at build time from the
   series JSON (`workflow.ts`):
   * **recommended** (pre-selected): `backtest.chosen_model`; the build fails unless it is the lowest-MAE candidate and
     equals `forecast.model`;
   * **amber, not recommended** (allowed / limited series): the other production models (`src/ailab/forecast.py`
     BASELINE + CANDIDATES: SeasonalNaive, Naive, MSTL, AutoTheta) with MAE, bar and the reason (error vs the
     recommendation, did not beat the baseline, baseline for comparison). Clicking one shows a small toast
     ("Demo mode: please choose the recommended method", `aria-live`) and keeps the recommendation;
   * **red, unavailable** (disabled), collapsed in one `<details>` "Unavailable methods (N)" below the open cards, each
     still a card with its reason: seasonal ETS ("statsforecast does not fit a seasonal ETS at weekly
     period 52", forecast.py / METHODS.md, checked against `series.season_length`);
   * red, one card each, in the same collapsed block: the methods tried in `benchmarks/forecast_bench.py` but not production
     candidates (SWA3, MSTL+AutoARIMA, ensembles MSTL+Theta, MSTL+Theta+SWA3, MSTL+Theta+Naive). `src/lib/bench.ts`
     recomputes, at build time from the committed `benchmarks/results/forecast.json`, each method's MAE relative to
     seasonal naive (geometric mean over seeds) pooled over all unmasked scenarios and on the COVID-like scenario, next to
     the current selection rule; for the two US ILINet series (same bundled FluView fixture as `real_wili` /
     `real_num_ili`) the per-series figure is shown too. The cards are rendered once and moved into the active series'
     collapsed block by JS (the count in its summary is updated); without JS they are a separate collapsed block after
     the series.
   Amber cards carry no tag; "recommended" and "unavailable" do.
   The build fails if `backtest.models` differs from the production set.
4. **Result** (only series with a recommended method): the decision badge (allowed / limited), the forecast band, the first and last forecast week with
   the 80% band, the outcome line, and the collapsed **Basis and limits** (the four recorded steps). The static site only
   publishes the forecast of the recommended method (hence the demo-mode lock). Showing every method's own forecast would need the export to write per-model `forecast` points
   and interval widths (`interval_widths(bt, model)` per candidate).

A stepper and Back buttons move between steps; choosing another series resets to step 1 and the recommended method.
Without JavaScript every series is listed with its overview, methods and result (step 1 and the buttons are hidden);
stopped series show only the overview, the red conclusion and Basis and limits.
Reproduce (CLI / `jq`) is secondary. `npm test` checks fidelity to the JSON, i18n, the home page structure, the ≤ +15 KB
home-page HTML budget vs 15f585e, and that the forecast page is not larger than the home page that carried the walkthrough
(a3e5190).
`SeriesChart` styles are global (prefixed `.chart`) so SVG nodes carry no `data-astro-cid` attribute, and history paths use
relative `l` steps between the same 0.1-rounded points (identical geometry, fewer bytes).


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
