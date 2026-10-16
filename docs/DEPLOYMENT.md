# Deployment

## Resource requirements (measured)

| | |
|---|---|
| image size | 660 MB (CPU only; pyarrow, scipy, statsmodels, DuckDB, pandas, numpy dominate) |
| memory | ≈ 290–300 MB process RSS with the demo data after several forecasts, also after a 5200-week series; allow 512 MB–1 GB headroom |
| CPU | analysis is single-threaded and serialised: one core is busy while a forecast runs (≈ 2 s for 6 years of weekly data, ≈ 8 s worst case with the 840-week cap); idle CPU ≈ 0 |
| disk | a few KB per series in DuckDB; demo ≈ 1 MB; static export of the demo ≈ 1.5 MB |
| start-up | ready in ≈ 3–10 s (importing statsforecast/numba dominates); restart with data in a volume ≈ 3 s |
| network | one port (8000); no outbound calls at run time (the demo uses a bundled fixture) |

## Run with Docker / Podman

```bash
export AILAB_API_KEY=$(openssl rand -hex 24)       # >= 16 chars; omit for a read-only service
docker run -d --name ailab --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  -e AILAB_API_KEY -v ailab-data:/data \
  ghcr.io/cnalks/ailab:latest
curl -fsS localhost:8000/ready
```

* Use a **named volume** (or a host directory owned by uid/gid `10001`) for `/data`; the image runs
  as the non-root user `10001:10001` and needs write access only to `/data`, `/out` and `/tmp`.
* Data survives `docker restart`, `docker stop/start` and re-creating the container on the same
  volume (covered by the e2e suite, including `kill -9`). Only **one** container may use a volume.
* Bind to `127.0.0.1` and put a reverse proxy (TLS, rate limiting, optional auth for reads) in front
  for anything public. The container has a `HEALTHCHECK` on `/health`.
* Podman: identical flags; `podman build --format docker -t ailab .` (the `HEALTHCHECK` instruction
  needs the docker image format).
* Pin a version in production: `ghcr.io/cnalks/ailab:v0.2.1` (tags `v*` are published by the
  `release-image` workflow; `latest` follows the newest tag).

## docker compose

[`docker-compose.yml`](../docker-compose.yml) runs the service with a named volume, a read-only root
filesystem, no capabilities and a healthcheck:

```bash
AILAB_API_KEY=$(openssl rand -hex 24) docker compose up -d      # writable; omit the variable for read-only
docker compose --profile batch run --rm demo                     # static export into ./out
```

Set `AILAB_CORS_ORIGINS=https://your.site` only if a browser page calls the API directly.

## Configuration

See the table in the [README](../README.md#configuration-environment-variables). Invalid values
(API key shorter than 16 characters, non-integer or out-of-range limits) make the process exit with
a message instead of starting in a weaker mode.

## Logs and monitoring

One line per request on stdout: `request_id=… METHOD /path -> status 12ms` (no query string, headers
or body; the configured key is redacted anywhere it appears). Probe `/ready` (503 when the database is
unreachable) for orchestrators and `/health` for dashboards (`uptime_s`, `db.latency_ms`).

## Backup

The state is one DuckDB file (`/data/ailab.duckdb`, plus a `.wal` while running). Stop the container
(or use `docker cp` after `docker stop`) and copy the file; or back up through the API
(`GET /api/v1/series/{id}/export?format=parquet`).

## Image and build notes

## Image
Multi-stage `Dockerfile`: build stage = `python:3.12-slim-bookworm` + `uv sync --frozen --no-dev
--no-editable` (hash-locked tree, third-party test suites and C headers removed); runtime stage =
same base image, no compiler, non-root `10001:10001`, `HEALTHCHECK` on `/health`, volumes `/data`
(DuckDB) and `/out`. Base image and uv are pinned **by tag and multi-arch index digest**
(both verified to exist by pulling them). To bump: `podman/docker pull <tag>`, read the index
digest (`podman images --digests`), update the two `ARG`s, rebuild, run the smoke test below.
The build context is limited by `.dockerignore` to `pyproject.toml uv.lock README.md src`.

Recommended runtime flags (also in `docker-compose.yml`): `--read-only --tmpfs /tmp --cap-drop ALL
--security-opt no-new-privileges`, bind `127.0.0.1` and put a reverse proxy (TLS, rate limit) in
front if exposed. Set `AILAB_API_KEY` (≥ 16 chars) only if you need write endpoints; set
`AILAB_CORS_ORIGINS` only for browser clients. See [API.md](API.md) for all variables.

## Smoke test (what CI and the local check run)
```bash
docker build -t ailab:dev .
docker run -d --name ailab --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:8000:8000 ailab:dev
curl -fs localhost:8000/health ; curl -fs localhost:8000/ready ; curl -fs localhost:8000/docs -o /dev/null
docker exec ailab id -u          # 10001
docker run --rm -v "$PWD/out:/out" ailab:dev demo --out /out      # static export
```

## Local verification record (2026-10-16)
The box had no Docker. Rootless **podman 5.4.2** (Debian 13 packages, crun, netavark/pasta,
fuse-overlayfs, user namespaces) was installed and used: `podman build --format docker` built the
image (≈ 15 s), size **660 MB** (venv ≈ 518 MB: pyarrow 161 MB, scipy 78+30 MB, DuckDB 58 MB,
pandas 39 MB, statsmodels 36 MB, numpy 26+27 MB; pyarrow is pulled in by statsforecast → fugue →
triad, so it cannot be dropped). The container ran with the hardened flags above; `/health`,
`/ready`, `/docs`, the container healthcheck (`healthy`), the policy/forecast endpoint, an authenticated
derive call, a rejected injection attempt, CORS and `ailab demo` all worked; `hadolint` 2.15.1
reports no findings. First build on GitHub Actions is still part of CI (`docker` job).
