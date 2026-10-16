# Deployment and container notes

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
