# ailab

**English** · [中文](#中文)

A small, reproducible **time-series engine for infectious-disease surveillance**:
store weekly series, derive new ones, forecast **only when a backtest says it is honest**
(every forecast carries a policy), raise statistical alert signals, and export everything as
static JSON/Parquet. Use it as a **library**, a **CLI / offline batch tool**, or a
**Docker-packaged service API** (FastAPI, embedded DuckDB — no Postgres, Redis, Celery or Supabase).

> Status: v0.1 MVP. Forecasts and alerts are statistical analysis of public data — not official
> products, not public-health advice.

## Quick start

```bash
# 1) Service in one command (demo data preloaded, read-only unless you set AILAB_API_KEY)
docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/cnalks/ailab      # or: docker compose up
curl localhost:8000/health          # -> {"status":"ok",...}
open http://localhost:8000/docs     # OpenAPI / Swagger UI

# 2) Offline batch -> static files for a website
docker run --rm -v "$PWD/out:/out" ghcr.io/cnalks/ailab demo --out /out

# 3) From source (Python 3.12, uv)
uv sync && uv run pytest && uv run ailab demo
```

(The image is published to GHCR by CI once the repository is public; until then use
`docker build -t ailab .` or `docker compose up --build`.)

## What it does

| Capability | Where |
|---|---|
| Series store (DuckDB file or in-memory, Parquet import/export; **missing = NULL, never 0**) | `ailab.store` |
| Derived series from safe expressions (`ma(x,4)`, `a / b * 100`, `diff`, `lag`, `pct`; AST whitelist, no `eval`) | `ailab.derive` |
| Data-quality checklist → grade A/B/C (length, gaps, zeros, timeliness) | `ailab.quality` |
| Forecast: SeasonalNaive baseline vs Naive / MSTL-ETS / Theta ([statsforecast](https://github.com/Nixtla/statsforecast)), **rolling-origin backtest**, empirical (split-conformal) intervals with leave-one-out coverage | `ailab.forecast` |
| **Forecast policy** `{forecast_allowed, reason, display_mode, model_family, horizon_weeks, indicator_type}`; a model must beat the seasonal-naive baseline in the backtest, and is only shown for horizons where it does | `ailab.policy` |
| Alert signals: EARS C1/C2/C3, CUSUM (statistical signals, not "outbreak" calls, no risk colours) | `ailab.alerts` |
| Static export (`manifest.json`, `series/<id>.json`, `downloads/*.parquet`, `sources.json`) with size budgets | `ailab.export` → [docs/EXPORT_FORMAT.md](docs/EXPORT_FORMAT.md) |

## CLI

```
ailab demo [--refresh] [--out out]     # full pipeline on CDC FluView demo series -> static export
ailab export --db my.duckdb --out out  # export your own stored series
ailab list | check <id> | forecast <id> --h 8 | alert <id> --method ears_c2
ailab serve --host 0.0.0.0 --port 8000 # service API
ailab doctor                           # environment self-check
```

## Service API (`ailab serve`)

`GET /health` · `GET /docs` (OpenAPI) · `GET /api/v1/series` · `POST /api/v1/series` ·
`GET /api/v1/series/{id}/observations|quality|forecast|policy|alerts` · `POST /api/v1/derive` ·
`GET|PUT|DELETE /api/v1/alerts/rules` · `GET /api/v1/alerts/evaluate` · `POST /api/v1/export`

Read endpoints are open. Write endpoints need header `X-API-Key` equal to env `AILAB_API_KEY`;
if the variable is unset the service is **read-only** (there is no default key). Analysis runs
in-process (one at a time, cached). Docker image: non-root (uid 10001), CPU-only, read-only root
filesystem supported, `HEALTHCHECK` on `/health`, pinned dependencies (`uv.lock`).

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

> 状态：v0.1 MVP。预测与告警是对公开数据的统计分析，非官方结论、非公共卫生建议。

### 快速开始

```bash
# 一条命令起服务（预载演示数据；未设置 AILAB_API_KEY 时为只读）
docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/cnalks/ailab     # 或 docker compose up
curl localhost:8000/health
# 浏览器打开 http://localhost:8000/docs 查看 OpenAPI 文档

# 离线批处理 → 生成网站用的静态文件
docker run --rm -v "$PWD/out:/out" ghcr.io/cnalks/ailab demo --out /out

# 源码运行（Python 3.12 + uv）
uv sync && uv run pytest && uv run ailab demo
```

### 预测策略（核心约定）

每个预测都携带 `forecast_allowed / reason / display_mode / model_family / horizon_weeks /
indicator_type`。规则：历史不足 3 个季节、稀疏事件类、数据缺口过长 → 只监测不预测；候选模型
必须在**滚动起点回测**中打败“季节朴素”基线，且只在确实打败基线的前若干周显示；区间由回测误差分位数
（split-conformal）给出，并报告留一法经验覆盖率。缺失值保持为空，**绝不补 0**。告警只称“统计信号”，
不使用红黄绿或“暴发”措辞。

### 许可

代码 Apache-2.0，版权 2026 [CNalks](https://github.com/CNalks)。**数据不适用该许可**，遵循各来源条款
（见 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)），每个导出序列都带有 `licence`、`attribution`
与必须随附的 `notice`。
