# 基准测试 / Benchmarks (v0.3)

> **English summary (short).** `benchmarks/` holds three offline, seeded harnesses: models/intervals
> (`forecast_bench.py`), alerts (`alert_bench.py`) and the quality gate (`gate_bench.py`); raw results
> are committed in `benchmarks/results/`. Findings: (1) the v0.2 candidate set + selection by backtest
> MAE (MSTL / AutoTheta / Naive vs. SeasonalNaive) is as good as anything we tried — ensembles, MSTL+ARIMA
> and a seasonal window average did **not** beat it on the pooled metric (relative MAE 0.74 vs 0.75–0.77),
> so the defaults are unchanged; (2) symmetric absolute-error conformal bands are slightly conservative
> (out-of-sample 80 % → 0.84, 95 % → 0.97), which we keep on purpose; (3) a Farrington-flexible-style
> quasi-Poisson alert at threshold 2 gives ≈1 false alarm per 100 clean weeks with 78 % detection of
> +100 % outbreaks, versus 8 false alarms for EARS C2 at similar detection — it is now the default
> alert for seasonal count/rate series; (4) the gate refuses all crafted bad series and none of the
> good seasonal ones (synthetic or real FluView). **Limits:** one real fixture (US ILINet, 2 series),
> synthetic seasonality is idealised, thresholds are heuristics, everything is in-silico.
> Reproduce: `uv run python benchmarks/<name>.py` (the gate harness takes ≈ 2 min; forecast and alert harnesses are
> much longer, use `--quick` for a smoke run).

本文档给出**测得**的结果，不是断言。所有脚本离线、固定随机种子、结果文件入库（`benchmarks/results/*.json`），
可用 `benchmarks/summarise_*.py` 复现表格。

## 1. 方法与数据

| 项目 | 内容 |
|---|---|
| 合成场景（真值已知） | 季节性计数（含/不含趋势、低计数）、季节性比例、非季节平稳、含离群点、"类 COVID"（一年压低到 5%）；每个场景 6 个种子，8 个季节（416 周） |
| 真实数据 | 随库附带的 CDC FluView 离线夹具（US ILINet：`wili`、`num_ili`，835 周）；另有两条"含 COVID 期"的截断版本，用于评估屏蔽（mask）选项 |
| 评估 | rolling-origin 交叉验证（h=8，每 3 周一个起点，每个起点只用此前数据重新拟合）；指标：MAE、WIS、80%/95% 覆盖率。区间为**样本外**：前 60% 起点校准，后 40% 起点评分（与校准期相邻的起点被剔除） |
| 比较方式 | 相对 SeasonalNaive 的 MAE/WIS（各种子几何平均，<1 更好）；`all` 为场景汇总 |

## 2. 预测模型

池化后的相对 MAE（vs SeasonalNaive，越小越好）：

| 模型 | 相对 MAE | 相对 WIS |
|---|---|---|
| SeasonalNaive | 1.00 | 1.00 |
| Naive | 1.57 | 1.17 |
| 季节窗口均值 SWA3 | 0.94 | 0.70 |
| **MSTL（ETS 趋势）** | **0.75** | 0.59 |
| MSTL + AutoARIMA | 0.76 | 0.60 |
| AutoTheta | 0.83 | 0.64 |
| 集成 MSTL+Theta | 0.77 | 0.60 |
| 集成 MSTL+Theta+SWA3 | 0.78 | 0.60 |

"在校准段上选模型、在留出段上评分"（更贴近线上做法：`select {MSTL, Theta, Naive}` = v0.2 规则）：
固定 MSTL 0.75，**选择规则 0.74**，加入 SWA3 0.75，加入集成 0.76–0.77。

**结论：** 候选集 `MSTL / AutoTheta / Naive`（按回测 MAE 选择，必须胜过 SeasonalNaive）保持不变。
集成、MSTL+ARIMA、SWA3 在汇总指标上没有稳定增益（部分场景如 covid_like 反而更差），不值得增加
拟合成本。注意：合成季节性是**严格周期**的，天然偏向季节模型，真实数据只有 2 条，这一结论
的证据强度有限。

### 区间校准

| 变体 | 80% 覆盖 | 95% 覆盖 | WIS（相对 abs） |
|---|---|---|---|
| **`abs`（现行：每个步长取绝对误差分位数，转换空间）** | 0.84 | 0.97 | 1.00 |
| `abs_mono`（宽度随步长单调） | 0.89 | 0.99 | 1.08 |
| `signed`（带符号分位数） | 0.78 | 0.92 | 0.91 |
| `abs_recent`（仅用较新 60% 误差） | 0.82 | 0.95 | 0.99 |

保留 `abs`：略偏保守（过宽比过窄更不危险），不会在小样本下欠覆盖；`signed` 的 WIS 更低但 80%/95%
欠覆盖；`abs_recent` 校准最好，但样本更少、更不稳定，增益在噪声内。**实测的短板：** 真实 `wili`/`num_ili`
用 MSTL 时 80% 区间样本外覆盖仅 0.76（其他模型 0.79–0.86），略低于名义值；门控的 `cov80_limit` 会在更差时撤下区间。

### COVID 屏蔽（`meta.mask_periods`）

在**非扰动**目标上比较有/无屏蔽（相对 MAE 更小更好）：合成 covid_like：MSTL 43.9 → **35.7**；
但在真实 FluView 上 `wili` 0.270 → 0.278、`num_ili` 6747 → 9081（**更差**）。所以屏蔽是**可选项，默认关闭**，
只在扰动明确且已知时使用；不应作为默认。

## 3. 预警方法

合成 8 个季节的季节性伽马-泊松计数（水平 20/150），评估段含 6 次已知起点的暴发（持续 8 周，峰值超额 +50%/+100%/+200%），
前三个季节另含 2 次污染基线的暴发。误报 = 非暴发周（及暴发后 2 周之外）的警报；POD = 暴发期内至少一次警报的比例。

现行默认阈值：

| 方法 | 阈值 | 误报 /100 个干净周 | POD +50% | +100% | +200% | 中位延迟（周） |
|---|---|---|---|---|---|---|
| EARS C1 | 3.0 | 4.4 | 53% | 70% | 90% | 1 |
| EARS C2 | 3.0 | 8.1 | 60% | 80% | 92% | 1 |
| EARS C3 | 2.0 | 18.8 | 74% | 88% | 95% | 1 |
| CUSUM | 4.0 | 8.1 | 72% | 87% | 95% | 1 |
| **Farrington-flexible 风格** | 2.0 | **1.1** | 52% | 78% | 90% | 2 |

**同误报率（≤2/100 干净周）下最小阈值的比较**：Farrington（阈值 1.75）POD +100% = **84%**，
EARS C1/C2/C3 仅 44%/44%/38%（阈值 4 / 5.75 / 11.5），CUSUM 在任何阈值下都达不到该误报率。
变体检验（`benchmarks/results/alerts_*.json`）：`overdispersed`（过度离散）与 `trend`（带趋势）上 Farrington
仍是误报最低（1.9 / 1.2 每 100 周，EARS C2 为 6.5 / 8.7）；但在 `flat`（**无季节性**）变体上
EARS C2 同样只有 2.0 误报且 POD +100% = 90%（Farrington 84%）——即 Farrington 的优势来自季节性，没有季节性时 EARS 足够好。

**取舍：** EARS/CUSUM 不建模季节性，在季节序列上天然高误报；因此季节性计数/率序列默认加入
`farrington`，EARS 阈值保持经典默认（不为"调出好看数字"而改动）。Farrington 延迟更大（中位 2 周），
且需要 ≥3 个季节的历史；这是用于"提示"而非"宣告暴发"。

**预警置信度与最小数据门控：** 每次扫描返回 `gate`（`allowed/limited/refused`）与 `confidence`；
基线观测周不足 5、历史短于基线+保护带、方法不适用（如比例序列用 Farrington）或基线近乎全零时
拒绝/抑制信号并给出原因码；历史不足 26 周评估期或不足 3 个季节则降级为 `degraded` 警告。

## 4. 质量门控的验证（`gate_bench.py`）

每个场景 8 个种子（阶段 1，数据检查）；完整流程（含回测）每场景 3 个种子。

| 类别 | 场景 | 阶段 1 结果 | 完整流程 |
|---|---|---|---|
| 好 | 季节（含锐峰/4 季节/趋势/低计数/3%缺失/过度离散/比例）、真实 wILI / num_ILI | **0 / 66 被拒绝**，0 被限制 | 0 被拒绝（4 季节序列带 `short_backtest_span` 警告；`num_ili` 带 `trend_nonstationary` 警告） |
| 坏 | 30 周、78 周、40% 缺失、常数、几乎全 0、尾部断更 30 周 | **全部拒绝**（原因码依次为 `too_short`、`too_short`、`too_many_gaps`、`constant_series`、`mostly_zeros`、`long_gap_at_end`） | 全部拒绝 |
| 难判断 | 随机游走 / 白噪声 / 末 80 周水平 ×3 | 随机游走 6/8 允许 + 警告，2/8 限制；白噪声允许 + `no_seasonal_signal…`；水平突变 8/8 限制（`structural_break`） | 随机游走与水平突变：限制；白噪声：允许（回测证明均值预测不差于基线，区间会很宽） |

"难判断"的诚实结论：**白噪声被允许**——判据是回测技能而不是"能否预测得好"，一个胜过基线的均值预测是合法的，
但门控会带出 `no_seasonal_signal_but_seasonal_expected` 警告。需要更严格时，可用 `min_seasonal_strength`
或 `max_rel_mae` 覆盖。

## 5. 已知局限

1. 真实数据只有一个来源（美国 ILINet 周数据，2 条序列），无法覆盖低计数、日度、月度的真实表现。
2. 合成季节性是理想的周期形状，对季节模型有利；`flat`/`outliers` 场景只做了稳健性检查。
3. 结构突变（sup-t ≥ 8 且 |位移| ≥ 0.5）、离群点（Hampel）、报告延迟尾部（同比对数差）阈值是**启发式**的，
   报告延迟检测功效低；建议通过 `reporting_lag_weeks` 显式声明最近 N 周为临时数据。
4. ADF/KPSS 在去季节后的序列上只作**警告**（对真实 ILINet 它们几乎总是报告"不平稳"），拒绝由回测技能决定。
5. 目前服务只接受周频序列；日/月频阈值表存在于库中，但只有单元测试覆盖。
6. Farrington 为简化实现（准泊松 GLM、2/3 次方变换、b=5 年、w=3 周、稳健离散度重加权），
   与 R `surveillance::farrington flexible` 未做逐点对照（该包需要 R）。
7. 所有结果为事后（回溯）评估，使用最终修订后的数据，不代表实时表现。

## 6. 复现

```bash
uv run python benchmarks/forecast_bench.py [--quick]     # -> benchmarks/results/forecast.json
uv run python benchmarks/summarise_forecast.py
uv run python benchmarks/alert_bench.py [--quick] [--variant base|flat|overdispersed|trend]
uv run python benchmarks/summarise_alerts.py [benchmarks/results/alerts_flat.json]
uv run python benchmarks/gate_bench.py [--seeds 8 --full-seeds 3]
```
