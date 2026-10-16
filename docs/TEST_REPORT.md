# 测试报告 / Test report — v0.2.1

> English summary at the bottom. 本报告由 `make e2e-report`（`scripts/e2e_report.py`）根据 `out/e2e-*.json` 生成的覆盖表加人工说明组成。
> 运行日期：2026-10-16（Asia/Shanghai）。

## 1. 范围与方法

- **单元/集成测试**：245 个（`make test`，每次 CI 运行）。
- **黑盒 e2e 测试**：504 个（`tests/e2e/`，`make e2e`），只通过 HTTP 访问真实运行的服务，不导入应用代码。两个目标：
  - `local`：子进程 `python -m ailab.cli serve`；
  - `container`：podman 容器，`--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges`，非 root，命名卷。
- **慢速/性能测试**：5 个（`make e2e-slow`），在两个目标上各跑一次。
- **测试数据**：`ailab synth testset` 生成 55 个确定性用例（20 个有效，含 1 个真实 FluView 全国 wILI 近 156 周切片；35 个非法），REST 加载器 `ailab synth load`。数据位于 `tests/data/`，由测试保证与生成器同步。
- **文档示例**：README/教程中的 ```` ```bash verify ````、```` ```python verify ```` 代码块由 `test_docs_samples.py` 在独立实例上执行，文档中的示例即实测通过的示例。
- 每个请求由记录客户端归入「端点 × 场景」表（下表数字为 通过/总数）。

## 2. 结果

| 目标 | 测试数 | 通过 | 失败 | 跳过 | HTTP 请求数 |
|---|---|---|---|---|---|
| local | 504 | 498 | 0 | 6 | 2623 |
| container | 504 | 503 | 0 | 1 | 2624 |
| local slow | 5 | 5 | 0 | 0 | 13 |
| container slow | 5 | 5 | 0 | 0 | 13 |

跳过的均为目标相关用例：local 跳过 6 个仅容器目标的检查（非 root、只读文件系统、无 capability、healthcheck、镜像内 CLI、重建容器保留卷数据）；container 跳过 1 个仅本地目标的检查（同一数据文件被第二个进程打开时安全失败）。不是失败。

全部 24 个已文档化的操作都有覆盖（见下表；`other` 行是恶意/未知路径与异常方法）。

## 3. 覆盖表（container 目标，完整运行）

| endpoint | system | auth | series CRUD/validation | synthetic test set | derive/safety | alerts | forecast/perf | export | limits/CORS/injection | concurrency | persistence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `GET /health` | 3/3 | 2/2 | · | · | 95/95 | · | 1/1 | · | 48/48 | 1/1 | 1/1 |
| `GET /ready` | 1/1 | 2/2 | · | · | · | · | · | · | · | 1/1 | · |
| `GET /docs` | 1/1 | 2/2 | · | · | · | · | · | · | · | · | · |
| `GET /openapi.json` | 2/2 | 2/2 | · | · | · | · | · | · | · | · | · |
| `GET /api/v1/series` | · | 2/2 | 12/12 | · | 1/1 | · | · | · | 38/38 | 3/3 | 3/3 |
| `POST /api/v1/series` | · | 8/8 | 35/35 | 35/35 | 120/120 | 25/25 | 13/13 | 8/8 | 8/8 | 7/7 | 3/3 |
| `GET /api/v1/series/{id}` | 2/2 | 3/3 | 4/4 | 53/53 | 97/97 | · | · | · | 23/23 | 1/1 | 2/2 |
| `DELETE /api/v1/series/{id}` | · | 9/9 | 24/24 | · | 120/120 | 25/25 | 13/13 | 8/8 | 25/25 | 7/7 | 2/2 |
| `GET /api/v1/series/{id}/data` | · | 2/2 | 7/7 | 20/20 | 5/5 | · | · | · | 30/30 | 5/5 | 3/3 |
| `GET /api/v1/series/{id}/observations` | · | · | 2/2 | · | · | · | · | · | · | · | · |
| `GET /api/v1/series/{id}/quality` | · | 2/2 | 2/2 | 20/20 | · | · | · | · | 21/21 | · | · |
| `GET /api/v1/series/{id}/lineage` | · | 2/2 | 2/2 | 20/20 | 1/1 | · | · | · | 21/21 | · | 1/1 |
| `GET /api/v1/series/{id}/forecast` | · | · | 1/1 | 19/19 | · | · | 15/15 | · | 21/21 | 1/1 | · |
| `GET /api/v1/series/{id}/policy` | · | · | 1/1 | 19/19 | · | · | 7/7 | · | 21/21 | · | · |
| `GET /api/v1/series/{id}/alerts` | · | 2/2 | 1/1 | 21/21 | · | 11/11 | · | · | 30/30 | · | · |
| `GET /api/v1/series/{id}/export` | · | 2/2 | 3/3 | 20/20 | · | · | · | 8/8 | 22/22 | · | · |
| `POST /api/v1/series/{id}/recompute` | · | 8/8 | · | · | 1/1 | · | · | · | · | · | · |
| `POST /api/v1/derive` | · | 8/8 | · | · | 9/9 | · | · | · | 1/1 | 1/1 | 1/1 |
| `POST /api/v1/derive/preview` | · | 1/1 | · | · | 121/121 | · | · | · | · | · | · |
| `GET /api/v1/alerts/rules` | · | 2/2 | · | · | · | 3/3 | · | · | 9/9 | · | 2/2 |
| `PUT /api/v1/alerts/rules` | · | 8/8 | · | · | · | 15/15 | · | · | · | · | 1/1 |
| `DELETE /api/v1/alerts/rules/{rule_id}` | · | 9/9 | · | · | · | 14/14 | · | · | · | · | · |
| `GET /api/v1/alerts/evaluate` | · | 2/2 | · | · | · | 3/3 | · | · | · | · | · |
| `POST /api/v1/export` | · | 10/10 | · | · | · | · | · | 3/3 | 1/1 | · | · |
| `other (hostile/unknown paths, odd methods)` | 2/2 | · | · | 1/1 | · | · | · | · | 13/13 | · | · |



## 4. 性能（慢速测试，单次测量，8 vCPU 的开发机；仅供量级参考）

| 场景 | local | container |
|---|---|---|
| 加载 55 个测试用例 | 1.5 s | 1.6 s |
| 预测：520 周（≈10 年周度）| 3.8 s | 3.8 s |
| 预测：1040 周 | 7.2 s | 7.5 s |
| 预测：2600 周 | 7.3 s | 8.0 s |
| 预测：5200 周 | 7.8 s | 8.3 s |
| 导出整个测试集（静态站点）| 33.4 s | 33.5 s |

说明：5200 周序列修复前预测耗时 **276 s**（并且 `/export` 会卡住）；现在模型输入最多取最近 840 周（`MAX_MODEL_WEEKS`），并在 policy `warnings` 与 `model_input.model_weeks_used/model_weeks_cap` 中明示。首次预测结果有 64 条 LRU 缓存，重复请求为毫秒级。

## 5. 发现并修复的问题（均有回归测试，见 `tests/test_regressions.py`）

1. **超长历史预测极慢**（5200 周 276 s，导出会挂起）→ 模型历史上限 840 周，默认演示数据（835 周）不受影响。
2. **`POST /export` 超出体积预算返回 500** → 现返回 422 `export_budget_exceeded`。
3. **并发创建同一 id 多个 201（非原子）** → `write_lock` 保护 check-then-write（create/delete/derive/recompute）。
4. **并发 DELETE + GET 偶发 500** → `KeyError` 映射为 404。
5. **`ailab serve` 配置非法时打印冗长 traceback** → 单行错误信息，退出码 2。

## 6. 已知限制与观察（未修改，已在文档中说明）

- 计数/率类序列在回溯季节数不足 3 个时仍允许预测，并附带警告；比例类则会拒绝。是否统一收紧见 `docs/MODEL_POLICY.md`。
- DuckDB 为单进程单文件：不能多个进程共享同一数据文件（部署文档已说明）。
- 导出整个测试集耗时约 33 s；`POST /export` 同步执行，大批量请考虑分批。
- 真实数据仅包含 1 个 FluView 切片；其他数据源见 README 的「数据来源」占位。

## 7. 如何复现

```bash
make test            # 245 unit/integration
make e2e             # 504 black-box tests against a local `ailab serve`
make e2e-slow        # timing tests
make image && make e2e-container   # same suite against the podman/docker container
make e2e-report      # regenerate the coverage tables from out/e2e-*.json
```

## English summary

- 245 unit/integration tests + 504 black-box e2e tests (HTTP only) run against both a local `ailab serve` process and a hardened container (read-only FS, non-root, all caps dropped): 498/503 passed, 0 failed, remaining are target-specific skips. 5 slow/perf tests pass on both.
- All 24 documented operations are covered across 11 scenarios (system, auth, CRUD/validation, 55-case synthetic + real FluView test set, derive safety with ~100 hostile expressions, alerts, forecast, export, limits/CORS/injection, concurrency, persistence).
- Performance: 10-year (520-week) weekly forecast ≈ 3.8 s; 100-year series ≈ 8 s after capping model history at 840 weeks (was 276 s).
- Five bugs found and fixed (slow long-history forecast, export-budget 500, non-atomic create, delete/read race 500, noisy config error), each with a regression test.
