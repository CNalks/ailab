# Contributing

Thanks for helping! The project is small; keep changes focused and tested.

```bash
git clone https://github.com/CNalks/ailab && cd ailab
uv sync                      # Python 3.12; dependencies are pinned in uv.lock
make test                    # unit + API tests with TestClient (~20 s)
make lint                    # ruff check + ruff format --check
make e2e                     # black-box API tests against a real `ailab serve` (~1.5 min)
make e2e-container           # same suite against the container (podman or docker)
make openapi                 # regenerate docs/openapi.json after API changes (a test checks it)
make testdata                # regenerate tests/data/synthetic (a test checks it)
```

* One logical change per commit; add a regression test for every bug fix (put API-level ones in
  `tests/test_regressions.py`, black-box ones under `tests/e2e/`).
* Code samples in README/docs marked `bash verify` / `python verify` are executed by `make e2e`;
  keep them working (and use unmarked fences for things that cannot run there, e.g. `docker run`).
* Style: ruff (line length 100), type-hinted, no new heavy dependencies without discussion.
* New data sources need a licence review first (see `docs/DATA_SOURCES.md`).
* No sensitive or political content in examples or data; use neutral geography names.
* By contributing you agree your work is licensed under Apache-2.0.
