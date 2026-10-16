# syntax=docker/dockerfile:1
# ailab — multi-stage, CPU-only, non-root. Build: docker build -t ailab .
# Pinned: base image tag (pin by digest in release builds), uv version, and uv.lock (hash-locked).

FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# 1) dependencies only (cached layer)
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
# 2) the project itself
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
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
