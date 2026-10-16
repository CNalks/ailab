# ailab

**English** · [中文](#中文)

A small, reproducible **time-series engine for infectious-disease surveillance**:
store weekly series, derive new ones, forecast **only when a backtest says it is honest**
(every forecast carries a policy), raise statistical alert signals, and export everything as
static JSON/Parquet. Use it as a **library**, a **CLI / offline batch tool**, or a
**Docker-packaged service API** (FastAPI, embedded DuckDB — no Postgres, Redis, Celery or Supabase).

> Status: v0.2 (backend + API; the showcase site is a later step). Forecasts and alerts are statistical analysis of public data — not official
> products, not public-health advice.

## Quick start

```bash
# 1) Service in one command (demo data preloaded, read-only unless you set AILAB_API_KEY, >= 16 chars)
docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/cnalks/ailab      # or: docker compose up
curl localhost:8000/health          # -> {"status":"ok","version":...,"db":{"ok":true}}
curl localhost:8000/ready           # -> {"status":"ready"}
open http://localhost:8000/docs     # OpenAPI / Swagger UI

# 2) Offline batch -> static files for a website
docker run --rm -v "$PWD/out:/out" ghcr.io/cnalks/ailab demo --out /out

# 3) From source (Python 3.12, uv)
uv sync && uv run pytest && uv run ailab demo
```

(The image is published to GHCR by CI once the repository is public; until then use
`docker build -t ailab .` or `docker compose up --build`. Built and smoke-tested locally with rootless
podman: 660 MB, non-root, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).)

## What it does

| Capability | Where |
|---|---|
| Series store (DuckDB file or in-memory, Parquet import/export; **missing = NULL, never 0**; kind-validated values) | `ailab.store` |
| **Series kinds** `indicator_type` = `count` / `proportion` / `rate` / `event`: kind-specific transform (log1p / logit), bounds, aggregation (ratio of sums for proportions) and forecast strictness | [docs/SERIES_KINDS.md](docs/SERIES_KINDS.md) |
| **Derived (composite) series** from safe expressions (`A + B`, `A / B` → proportion, `rolling_mean(A, 7)`, `lag`, `sum(a,b,c)`, `rolling_ratio`): [`simpleeval`](https://github.com/danthedeckie/simpleeval) + AST whitelist (no `eval`/`exec`), automatic kind inference, explicit alignment/missing rules, stored lineage | `ailab.derive`, `ailab.derive_service` → [docs/DERIVED_SERIES.md](docs/DERIVED_SERIES.md) |
| Synthetic seeded data for tests/dev (seasonality, gaps, outbreaks, counts, proportions) | `ailab.synth` |
| Data-quality checklist → grade A/B/C (length, gaps, zeros, timeliness) | `ailab.quality` |
| Forecast: SeasonalNaive baseline vs Naive / MSTL-ETS / Theta ([statsforecast](https://github.com/Nixtla/statsforecast)), **rolling-origin backtest spanning ≥ 3 seasons by default**, empirical (split-conformal) intervals in transformed space with leave-one-out coverage | `ailab.forecast` |
| **Forecast policy** `{forecast_allowed, reason, display_mode, model_family, horizon_weeks, indicator_type}`; a model must beat the seasonal-naive baseline in the backtest, and is only shown for horizons where it does | `ailab.policy` |
| Alert signals: EARS C1/C2/C3, CUSUM (statistical signals, not "outbreak" calls, no risk colours) | `ailab.alerts` |
| Static export (`manifest.json`, `series/<id>.json`, `downloads/*.parquet`, `sources.json`) with size budgets | `ailab.export` → [docs/EXPORT_FORMAT.md](docs/EXPORT_FORMAT.md) |

## CLI

```
ailab demo [--refresh] [--out out]     # full pipeline on CDC FluView demo series -> static export
ailab export --db my.duckdb --out out  # export your own stored series
ailab list | check <id> | forecast <id> --h 8 | alert <id> --method ears_c2
ailab series add|list|show|data|delete  --db my.duckdb   # base series (kind required: --kind count|proportion|rate|event)
ailab derive preview|create|recompute   --db my.duckdb   # derived series, e.g.
ailab derive create pos_rate "pos / tests" --name "Positivity" --db my.duckdb
ailab derive create pct "100 * A / B" --bind A=pos --bind B=tests --name "Positivity %" --db my.duckdb
ailab synth --kind count --weeks 300 --seed 1 --out x.csv   # deterministic synthetic series
ailab serve --host 0.0.0.0 --port 8000 # service API
ailab doctor                           # environment self-check
```

## Service API (`ailab serve`)

`GET /health` (version, DB check) · `GET /ready` · `GET /docs` (OpenAPI) ·
`GET|POST /api/v1/series` · `GET|DELETE /api/v1/series/{id}` ·
`GET /api/v1/series/{id}/data|quality|lineage|forecast|policy|alerts|export` ·
`POST /api/v1/derive` · `POST /api/v1/derive/preview` · `POST /api/v1/series/{id}/recompute` ·
`GET|PUT|DELETE /api/v1/alerts/rules` · `GET /api/v1/alerts/evaluate` · `POST /api/v1/export`.
Full table, errors, limits and environment variables: [docs/API.md](docs/API.md).

Hardening: reads are open; **all writes need `X-API-Key` = env `AILAB_API_KEY` (≥ 16 chars), and
without that variable the service is read-only** (no default key). Structured JSON errors with request
ids (no tracebacks, input never echoed), request-size limit (`AILAB_MAX_BODY_BYTES`, 2 MiB), pagination
limits, CORS off unless `AILAB_CORS_ORIGINS` is set, logs without headers/bodies/query strings/keys, no
pickle (models are never stored). Image: non-root (uid 10001), CPU-only, read-only root filesystem
supported, `HEALTHCHECK`, pinned base digests and dependencies (`uv.lock`).

## Data and licences

Code: **Apache-2.0**, Copyright 2026 [CNalks](https://github.com/CNalks). **Data are not covered
by that licence**: each dataset follows its source's terms ([docs/DATA_SOURCES.md](docs/DATA_SOURCES.md));
every exported series carries `licence`, `attribution` and a mandatory `notice`.
The bundled test fixture is the CDC FluView national series retrieved via the Delphi Epidata API.

---

## 中文

**ailab** 是一个轻量、可复现的**传染病监测时间序列引擎**：管理周序列、生成派生序列、
**只有回测证明“诚实”才给出预测**（每个预测都带策略元数据）、产出统计告警信号，并把结果导出为
静态 JSON/Parquet。可作为**库**、**CLI/离线批处理**，或 **Docker 打包的服务 API**（FastAPI + 内嵌
DuckDB，**不需要** Postgres / Redis / Celery / Supabase）。

> 状态：v0.2（仅后端与 API；展示站为后续步骤）。预测与告警是对公开数据的统计分析，非官方结论、非公共卫生建议。

### 快速开始

```bash
# 一条命令起服务（预载演示数据；未设置 AILAB_API_KEY（≥16 字符）时为只读）
docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/cnalks/ailab     # 或 docker compose up
curl localhost:8000/health   # 版本 + 数据库检查
curl localhost:8000/ready    # 就绪探针
# 浏览器打开 http://localhost:8000/docs 查看 OpenAPI 文档

# 离线批处理 → 生成网站用的静态文件
docker run --rm -v "$PWD/out:/out" ghcr.io/cnalks/ailab demo --out /out

# 源码运行（Python 3.12 + uv）
uv sync && uv run pytest && uv run ailab demo
```

### 序列类型、派生序列与服务 API

- **序列类型 `indicator_type`**：`count` / `proportion` / `rate` / `event`，创建时必须显式给出。同一引擎，差异：
  比例类用 logit 变换，预测与区间始终落在 `[0, value_scale]`，聚合只能用**和之比**（禁止直接平均），
  且预测门槛更严（须在回测中以 ≥5% 的余量打败季节朴素基线、领先 ≥2 个步长、回测覆盖 ≥3 个季节、80% 区间覆盖 ≥70%）；
  计数类用 log1p，结果非负。详见 [docs/SERIES_KINDS.md](docs/SERIES_KINDS.md)。
- **自定义/派生序列**：对已有序列写表达式（`A + B`、`A / B`→比例、`rolling_mean(A, 7)`、`lag`、`sum(a,b,c)`、
  `rolling_ratio(分子, 分母, n)`），用成熟库 `simpleeval` + AST 白名单（绝不 `eval`/`exec`），自动推断类型，
  日期/频率对齐规则明确，**缺失绝不静默补 0**，保存血缘（来源序列、对齐报告、类型推断轨迹）并持久化到 DuckDB。
  CLI：`ailab derive create|preview|recompute`、`ailab series add|list|show|data|delete`；REST：`POST /api/v1/derive` 等。
  详见 [docs/DERIVED_SERIES.md](docs/DERIVED_SERIES.md)、[docs/API.md](docs/API.md)。
- **加固**：所有写接口需 `X-API-Key`（环境变量 `AILAB_API_KEY`，≥16 字符；未设置即只读，无默认口令）；结构化错误（带
  request id，不回显输入、不泄露堆栈）、请求体大小限制、分页上限、CORS 默认关闭（`AILAB_CORS_ORIGINS` 配置）、
  日志不含请求头/请求体/查询串/密钥、不使用 pickle。
- **测试**：确定性（带种子）合成序列生成器 `ailab.synth`（季节性、缺失周、暴发尖峰、计数与比例）用于单元测试，仅保留一份真实
  FluView 夹具做端到端测试（[docs/TESTING.md](docs/TESTING.md)）。
- **容器**：已用 rootless podman 在本机构建并冒烟测试（镜像 660 MB，非 root，`/health` `/ready` `/docs` `ailab demo` 均通过），
  见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)；首次 GitHub Actions 构建仍由 CI 完成。

### 预测策略（核心约定）

每个预测都携带 `forecast_allowed / reason / display_mode / model_family / horizon_weeks /
indicator_type`。规则：历史不足 3 个季节、稀疏事件类、数据缺口过长 → 只监测不预测；候选模型
必须在**滚动起点回测（默认覆盖 ≥3 个季节）**中打败“季节朴素”基线，且只在确实打败基线的前若干周显示；区间由回测误差分位数
（split-conformal）给出，并报告留一法经验覆盖率。缺失值保持为空，**绝不补 0**。告警只称“统计信号”，
不使用红黄绿或“暴发”措辞。

### 许可

代码 Apache-2.0，版权 2026 [CNalks](https://github.com/CNalks)。**数据不适用该许可**，遵循各来源条款
（见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)），每个导出序列都带有 `licence`、`attribution`
与必须随附的 `notice`。
