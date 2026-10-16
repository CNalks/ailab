# Common tasks. `uv` is required (https://docs.astral.sh/uv/).  Container targets use podman if
# installed, else docker (override: make e2e-container ENGINE=docker).
ENGINE ?= $(shell command -v podman >/dev/null 2>&1 && echo podman || echo docker)
IMAGE  ?= ailab:local

.PHONY: test lint fmt e2e e2e-container e2e-all e2e-report image openapi testdata

test:            ## fast unit + API tests (TestClient), ~20 s
	uv run pytest

lint:
	uv run ruff check src tests scripts && uv run ruff format --check src tests scripts

fmt:
	uv run ruff check --fix src tests scripts && uv run ruff format src tests scripts

e2e:             ## black-box API tests against a real `ailab serve` subprocess (~1-2 min)
	AILAB_E2E_TARGET=local uv run pytest tests/e2e -m "e2e" -p no:cacheprovider

e2e-slow:        ## same plus the slow performance tests (10-year / 100-year series)
	AILAB_E2E_TARGET=local uv run pytest tests/e2e -m "e2e or slow" -p no:cacheprovider

image:
	$(ENGINE) build $(if $(filter podman,$(ENGINE)),--format docker,) -t $(IMAGE) .

e2e-container: image   ## same suite against the container (read-only fs, non-root, volumes)
	AILAB_E2E_TARGET=container AILAB_E2E_ENGINE=$(ENGINE) AILAB_E2E_IMAGE=$(IMAGE) \
	  uv run pytest tests/e2e -m "e2e" -p no:cacheprovider

e2e-all: e2e-slow e2e-container e2e-report

e2e-report:      ## summarise out/e2e-*.json as a markdown coverage table
	uv run python scripts/e2e_report.py

openapi:         ## regenerate docs/openapi.json
	uv run python scripts/gen_openapi.py

testdata:        ## regenerate tests/data/synthetic (deterministic)
	uv run ailab synth testset --out tests/data/synthetic
