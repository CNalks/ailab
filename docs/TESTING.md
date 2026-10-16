# Testing

`uv run pytest` (≈ 20 s) · `uv run ruff check src tests && uv run ruff format --check src tests`.

* **Synthetic data** — `ailab.synth.synth_series / synth_pair` generate seeded, deterministic weekly
  series: seasonality (sharpness, amplitude), trend, negative-binomial counts, binomial proportions
  with varying denominators (fraction or percent), outbreak spikes, missing weeks (NULL, never 0) and
  numerator/denominator pairs with `positives ≤ tests`. Unit tests use a 13-week season so the
  backtest takes milliseconds. `ailab synth --kind count --weeks 300 --seed 1 --out x.csv` writes one.
* **One real fixture** — `src/ailab/data/fluview_nat_fixture.csv` (CDC FluView national, 835 weeks)
  is used by `tests/test_e2e_fluview.py` (and by `ailab demo`) only.
* Coverage: parser safety incl. ~70 injection attempts, kind inference table, ratio-of-sums,
  alignment/missing semantics, transforms and bounds, policy decisions (stubbed backtests), derived
  series persistence/lineage/conflicts, REST endpoints, auth/read-only defaults, size limits, CORS,
  structured errors, log hygiene.
