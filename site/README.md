# ailab showcase site

Static Astro site (zh / en / fr) served at `/ai4health/lab/`. See [../docs/SHOWCASE_SITE.md](../docs/SHOWCASE_SITE.md).

```bash
uv run python ../scripts/build_showcase_data.py   # precompute public/data with the real ailab code (from the repo root: uv run python scripts/build_showcase_data.py)
npm ci && npm run dev                             # http://localhost:4321/ai4health/lab/
npm run build                                     # astro check + static build -> dist/
```
