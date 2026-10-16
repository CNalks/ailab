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

## Refresh data
```bash
# 1. update the snapshots in site/data-src/ (see provenance.json for the exact request URLs and filters)
# 2. recompute
uv run python scripts/build_showcase_data.py      # -> site/public/data (fails if the size budget is exceeded)
# 3. build
cd site && npm ci && npm run build               # -> site/dist
```

## Deploy (static files only)
`site/dist/` is the complete artefact for `/var/www/ai4health/lab/`; nginx needs `location ^~ /ai4health/lab/ { root /var/www; ... }`
(HTML `no-cache`, `/ai4health/lab/_astro/` immutable, `/ai4health/lab/data/` 10 min). Deploy as for the other static sites:
tar over ssh into a `.new` directory, `chown -R www-data`, keep the previous directory as a backup, atomic swap, `nginx -t && reload`.
The badge expects `location = /ailab-api/health` proxying to the ailab service (GET/HEAD only).
