# syntax=docker/dockerfile:1
# ailab — multi-stage, CPU-only, non-root. Build: docker build -t ailab .   (podman build --format docker also works)
# Pinned: base image (tag + multi-arch index digest, verified to exist), uv (tag + digest), and
# uv.lock (hash-locked dependency tree). Bump deliberately; see docs/DEPLOYMENT.md.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app
# 1) dependencies only (cached layer)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
# 2) the project itself (non-editable: the venv is self-contained)
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
 # 3) trim what is never needed at runtime (test suites of third-party packages, C headers)
 && find /app/.venv -type d \( -name tests -o -name test -o -name testing \) -path '*/site-packages/*' \
      ! -path '*/numpy/testing' ! -path '*/pandas/_testing' ! -path '*/pyarrow/tests' -prune -exec rm -rf {} + \
 && find /app/.venv -type d -name include -path '*/site-packages/*' -prune -exec rm -rf {} + \
 && /app/.venv/bin/python -c "import ailab, statsforecast, duckdb, pandas, fastapi; print('imports ok')"

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="ailab" \
      org.opencontainers.image.description="Time-series engine for infectious-disease surveillance" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/CNalks"
RUN groupadd --system --gid 10001 ailab \
 && useradd --system --uid 10001 --gid ailab --home-dir /home/ailab --create-home --shell /usr/sbin/nologin ailab \
 && mkdir -p /data /out && chown ailab:ailab /data /out
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    AILAB_DB=/data/ailab.duckdb AILAB_OUT=/out AILAB_PRELOAD_DEMO=1
USER 10001:10001
WORKDIR /home/ailab
VOLUME ["/data", "/out"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["ailab"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
