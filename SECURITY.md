# Security policy

## Supported versions
The latest release (`main` and the newest `v0.2.x` image tag).

## Reporting a vulnerability
Please **do not** open a public issue. Use GitHub's private advisory form
(<https://github.com/CNalks/ailab/security/advisories/new>) or email linzhijian.cn@gmail.com.
Include the version, a minimal reproduction and the impact. You will get an acknowledgement within a
few days; this is a small project maintained on a best-effort basis.

## Security model (what is and is not protected)
* Reads are open; writes need `X-API-Key` (≥ 16 characters, constant-time comparison); no key ⇒
  read-only. There is no user management, no rate limiting and no TLS: run behind a reverse proxy.
* Derived-series expressions are parsed with an AST whitelist and `simpleeval`; no `eval`/`exec`;
  no attribute access, subscripts, strings or `**`. Limits: 300 characters, 80 nodes, 8 sources.
* All SQL uses bound parameters; ids are restricted to `^[a-z0-9][a-z0-9_]{0,63}$`.
* Errors never include tracebacks or echo request bodies; logs contain no headers, bodies, query strings or keys.
* No pickle/joblib; models are never persisted.
* Container: non-root (10001), works with a read-only root filesystem and `--cap-drop ALL`.
* Request bodies are limited (default 2 MiB), pagination and observation counts are capped.

Known limits: a single authenticated client can occupy the single compute slot with expensive
requests (forecasts take seconds); CPU/memory limits and rate limiting belong in the deployment.
