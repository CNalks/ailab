# ailab

[![CI](https://github.com/CNalks/ailab/actions/workflows/ci.yml/badge.svg)](https://github.com/CNalks/ailab/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Image: ghcr.io/cnalks/ailab](https://img.shields.io/badge/image-ghcr.io%2Fcnalks%2Failab-2496ED?logo=docker&logoColor=white)](https://github.com/CNalks/ailab/pkgs/container/ailab)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)

[English](README.md) · **简体中文**

**ailab** 是一个轻量、可复现的**传染病监测时间序列引擎**：管理周序列、用安全表达式生成派生序列、
**只有回测证明预测“诚实”才给出预测**（每个预测都带机器可读的策略元数据）、产出统计告警信号，并把结果导出为静态 JSON/Parquet。
可作为**库**、**命令行/离线批处理**，或 **Docker 打包的 REST 服务**（FastAPI + 内嵌 DuckDB，
**不需要** Postgres / Redis / Celery / Supabase）。

> **不得用于临床或公共卫生决策。** 预测与告警是对公开数据的统计分析，不是官方产品。见下文[局限与诚实声明](#局限与诚实声明)。

## 快速开始

```bash
# 带演示数据的只读服务（未设置 API Key ⇒ 所有写接口返回 403）
docker run --rm -p 127.0.0.1:8000:8000 ghcr.io/cnalks/ailab

# 可写：自选至少 16 个字符的密钥
export AILAB_API_KEY=$(openssl rand -hex 24)
docker run --rm -p 127.0.0.1:8000:8000 -e AILAB_API_KEY ghcr.io/cnalks/ailab

# 加固运行（测试套件即以此方式测试），数据放在卷里：
docker run -d --name ailab -p 127.0.0.1:8000:8000 --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges -e AILAB_API_KEY -v ailab-data:/data ghcr.io/cnalks/ailab
```

也可 `docker compose up`（见 [docker-compose.yml](docker-compose.yml)）。然后：

```bash verify
export AILAB_URL=${AILAB_URL:-http://127.0.0.1:8000}
curl -fsS "$AILAB_URL/health" | jq -c '{status, version}'
curl -fsS "$AILAB_URL/api/v1/series/us_ilinet_wili/policy" | jq -c '{forecast_allowed, reason, model_family, horizon_weeks}'
```

```
{"status":"ok","version":"0.3.0"}
{"forecast_allowed":true,"reason":"beats_seasonal_naive_in_rolling_origin_backtest","model_family":"mstl_ets","horizon_weeks":8}
```

交互式 API 文档：<http://127.0.0.1:8000/docs>。**接着看 [5 分钟教程](docs/TUTORIAL.md)**（英文；创建序列、派生、预测、告警、导出，
其中每条命令都由测试套件实际执行）。

不用 Docker（Python 3.12 + [uv](https://docs.astral.sh/uv/)）：

```bash
git clone https://github.com/CNalks/ailab && cd ailab
uv sync && uv run pytest              # 约 20 秒
uv run ailab serve                     # http://127.0.0.1:8000（加 --preload-demo 载入演示数据）
uv run ailab demo --out out            # 离线批处理：导出演示序列的静态文件
```

## 核心概念

**序列类型。** 每个序列都有显式的 `indicator_type`，创建时必填：`count`（非负计数）、`proportion`（比例，取值在
`[0, value_scale]`，`value_scale`=1 为小数、100 为百分数）、`rate`（其他度量；`-`/`diff` 产生的可为负）、`event`（稀疏事件，
只监测、不预测）。模型在按类型选择的变换空间中拟合（计数/比率用 `log1p`，比例用 `logit`），因此预测和区间端点始终落在合法范围内。
比例只能用**和之比**聚合（禁止直接平均），预测门槛也更严。详见 [SERIES_KINDS.md](docs/SERIES_KINDS.md)。

**派生序列。** `POST /api/v1/derive` 对已有序列求值表达式，如 `positives / tests`（→ 比例）、`100 * A / B`（→ 百分数）、
`rolling_ratio(pos, tests, 4)`、`rolling_mean(cases, 7)`、`lag(A, 52)`、`sum(a, b, c)`。求值使用 Python AST 白名单加
`simpleeval` 库，**不使用 `eval`/`exec`**。结果类型自动推断（也可显式断言）；缺失周保持缺失，`x/0` 为缺失（绝不是 ∞），并保存
**血缘**（来源序列、对齐报告、类型推断轨迹）。派生序列是快照，来源变化后需 `POST …/recompute`。

**预测策略。** 每个预测响应都带有：

| 字段 | 含义 |
|---|---|
| `forecast_allowed` | `false` ⇒ 不返回任何预测数值，只给 `reason` |
| `reason` | 机器可读原因，如 `beats_seasonal_naive_in_rolling_origin_backtest`、`history_lt_3_seasons`、`data_gaps_too_long`、`sparse_series`、`no_skill_vs_seasonal_naive`、`indicator_type_event_sparse`、`band_undercoverage_in_backtest` |
| `display_mode` | `forecast_band`（点预测 + 80/95% 区间）· `forecast_point_only`（回测覆盖率差，隐藏区间）· `monitor_only` · `history_only` |
| `model_family` | `mstl_ets` · `theta` · `naive` · `seasonal_naive` · `none` |
| `horizon_weeks` | 回测中持续优于季节朴素基线的前若干周（不超过请求的步长） |
| `indicator_type` | 规则所依据的序列类型 |
| `warnings` | 非致命提示（回测偏短、步长被截断、数据质量 B 级、历史截断……） |
| `gate_decision`、`reason_codes`、`limited` | （0.3 新增）质量门控结论 `allowed`/`limited`/`refused` 与机器可读原因码；完整的 `gate` 对象与 `policy` 同级 |

候选模型必须在滚动起点回测（默认覆盖 ≥ 3 个季节，每个起点只用之前的数据重新拟合）中打败**季节朴素**基线。详见
[MODEL_POLICY.md](docs/MODEL_POLICY.md)。

**质量门控（0.3 新增）。** 在给出任何数字之前，门控先判断“这条序列能不能预测”，并说明原因：历史太短、缺失太多、尾部断更、
常数/几乎全零、结构突变、方差不稳、离群点、报告延迟、回测不优于季节朴素基线、区间欠覆盖等，每项都有**机器可读原因码**、
中/英/法三语说明、实测指标与所用阈值、建议动作。结论为 `allowed`、`limited`（缩短步长/加宽区间/只用基线模型）或 `refused`。
“非平稳/难判断”只在去季节后给**警告**（季节性流行不会被误拒），是否拒绝由回测技能决定。拒绝时 HTTP 仍为 200，`forecast: null`。
用法：`GET /series/{id}/gate`、`ailab gate <序列>`；阈值可按类型/频率/序列、环境变量或 `meta.gate` 覆盖。
详见 [MODEL_POLICY.md](docs/MODEL_POLICY.md#quality-gate-v03) 与实测结果 [BENCHMARK.md](docs/BENCHMARK.md)。

**告警规则。** EARS C1/C2/C3（美国 CDC）与标准化 CUSUM，把每周与此前若干周（默认 7 周）的基线比较。它们只是**统计信号**，
不是“暴发”判定，不带风险颜色，EARS/CUSUM **不建模季节性**（正常的季节性上升也会触发）；0.3 新增的 Farrington 风格告警（`method=farrington`）建模季节性，是季节性计数序列的默认告警之一（实测：约 1 次误报/100 个干净周，EARS C2 约 8 次）。告警也有自己的门控与置信度。可用即席扫描
（`GET /series/{id}/alerts`）或保存规则（`PUT /alerts/rules`、`GET /alerts/evaluate`）。

**数据质量评分。** 透明的检查清单（长度 ≥ 3 个季节、完整度、最长缺口 ≤ 2 周、非零占比 ≥ 50%、连续零、负值）→ 等级
**A**（全部通过）、**B**（仅有警告）、**C**（有阻断项 ⇒ 不预测）。不是黑盒打分。

**导出格式。** `manifest.json` + `series/<id>.json`（数据、质量、策略、回测、预测、告警）+ `downloads/<id>.parquet` +
`sources.json`，带体积预算：[EXPORT_FORMAT.md](docs/EXPORT_FORMAT.md)。单个序列也可经 API 下载为 CSV/JSON/Parquet。

**鉴权模型。** 读接口开放；**所有写接口**（创建/删除序列、派生、重算、告警规则、导出）都要求请求头 `X-API-Key` 等于
`AILAB_API_KEY`（≥ 16 字符，常数时间比较）。未设置 `AILAB_API_KEY` 时服务**只读**（写 → 403）；没有默认密钥；密钥过短则拒绝启动。
对外暴露前请在反向代理处加 TLS、限流以及读接口的鉴权。

## 配置（环境变量）

| 变量 | 默认 | 含义 |
|---|---|---|
| `AILAB_DB` | 内存（镜像内 `/data/ailab.duckdb`） | DuckDB 文件；一个文件只能一个进程 |
| `AILAB_API_KEY` | 未设置 ⇒ 只读 | 写密钥，≥ 16 字符 |
| `AILAB_PRELOAD_DEMO` | `0`（镜像内 `1`） | 空库时载入内置 CDC FluView 演示数据 |
| `AILAB_OUT` | `out`（镜像内 `/out`） | `POST /api/v1/export` 的输出目录 |
| `AILAB_MAX_BODY_BYTES` | 2 MiB | 请求体上限（413） |
| `AILAB_MAX_OBSERVATIONS` | 5200 | 单个序列创建时的观测数上限 |
| `AILAB_MAX_SERIES` | 500 | 库中序列数上限 |
| `AILAB_CORS_ORIGINS` | 空（关闭 CORS） | 逗号分隔的允许来源 |
| `AILAB_LOG_LEVEL` | `INFO` | 每个请求一行访问日志，不含请求头/请求体/查询串/密钥 |

## 资源占用（实测）

镜像 **660 MB**（仅 CPU，非 root，uid 10001）。带演示数据并做过若干次预测后，进程常驻内存约 **290–300 MB**（含 5200 周序列的预测之后）。
分析是单线程且串行的（同一时间只算一个预测；读取、健康检查、写入保持响应）。单核上首次预测耗时（重复请求走缓存）：6 年周数据约 2 秒，
10 年约 4 秒，≥ 16 年约 7–8 秒（模型只使用最近 840 周）。存储极小（每个序列几 KB）。测量方法见 [TEST_REPORT.md](docs/TEST_REPORT.md)。

## 命令行

```
ailab serve [--host --port --db --preload-demo]         # REST 服务
ailab demo [--refresh] [--out out]                       # 演示序列 → 静态导出
ailab series add|list|show|data|delete --db my.duckdb   # 管理基础序列（必须给出类型）
ailab derive preview|create|recompute --db my.duckdb    # 派生序列
ailab list | check <id> | gate <id> | forecast <id> --h 8 | alert <id> --method ears_c2
ailab export --db my.duckdb --out out                    # 导出自己的序列
ailab synth --kind count --weeks 300 --seed 1 --out x.csv    # 单个确定性合成序列
ailab synth testset --out tests/data/synthetic                # 完整的 API 测试数据集
ailab synth load --url http://127.0.0.1:8000 --key $AILAB_API_KEY   # 经 REST 载入运行中的服务
ailab doctor                                              # 环境自检
```

## 局限与诚实声明

- **不是决策工具。** 不得用于临床、公共卫生或政策决策；输出是未经审核的公开数据统计结果。
- **回测偏乐观。** 模型在同一个滚动起点回测上被*选择*，又用它报告成绩（选择偏差）；起点之间有重叠（步长 3 周），窗口相关；
  约 3 个季节只有约 3 个峰值。报告的技能只是参考，不是保证。
- **忽略数据修订。** 回测使用最终（最新版本）数值，而非当时已知的数据；真实报告延迟与回填未建模，实时表现会更差。
- **区间是边际的。** 回测误差的经验分位数混合了高峰与淡季；虽做了留一窗口的覆盖率检验，但不保证任一具体季节的覆盖。区间仅供参考。
- **计数/比率类即使回测不足 3 个季节也可能被预测**（`policy.warnings` 会提示）；比例类此时会被拒绝。
- **历史截断。** 为限制计算时间，模型只拟合最近 840 周（约 16 年）。
- **EARS/CUSUM 告警不考虑季节性**（可用 `farrington`），每年冬季出现信号属正常；它们只表示统计偏离，不代表暴发。
- **仅支持周数据**（每个序列一套星期网格，`season_length` 为 2–60），默认每序列最多 5200 个观测。
- **单节点。** 内嵌 DuckDB 一个数据库文件只允许一个服务进程；分析串行；无集群、无读鉴权、无限流（请用反向代理）。
- 内置演示数据是 CDC FluView 全国序列的**固定快照**，不是实时数据。

## 数据来源与许可

数据**不**适用代码许可；各数据集遵循其来源条款，每个序列都带 `licence`、`attribution` 与 `notice`。详见
[DATA_SOURCES.md](docs/DATA_SOURCES.md)。内置演示快照为经 Delphi Epidata API 获取的 CDC FluView（ILINet）全国周数据
（公有领域，需署名 CDC；确切条款见该文件）。*其他数据源：占位——暂未启用。*

## 文档

[教程](docs/TUTORIAL.md) · [API 参考](docs/API.md)（[openapi.json](docs/openapi.json)）· [架构](docs/ARCHITECTURE.md) ·
[模型策略](docs/MODEL_POLICY.md) · [方法](docs/METHODS.md) · [序列类型](docs/SERIES_KINDS.md) ·
[派生序列](docs/DERIVED_SERIES.md) · [导出格式](docs/EXPORT_FORMAT.md) · [部署](docs/DEPLOYMENT.md) ·
[FAQ](docs/FAQ.md) · [测试](docs/TESTING.md) · [测试报告（中文）](docs/TEST_REPORT.md) · [数据来源](docs/DATA_SOURCES.md)

## 贡献与安全

欢迎贡献：见 [CONTRIBUTING.md](CONTRIBUTING.md)。漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可

代码：[Apache-2.0](LICENSE)，© 2026 [CNalks](https://github.com/CNalks)。数据：见上。
