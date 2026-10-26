// Build-time model of the "run the gate on one series" walkthrough shown right after the hero.
// Every value is read from site/public/data (never hard-coded or mocked). assertRun() fails the build when the static
// artefacts disagree with each other, or when the step-by-step view could not be derived from the recorded gate result
// (unknown reason code, a block without a refusal, a backtest that ran although the data checks blocked, ...).
import { manifest, series, SERIES_IDS, type GateReason, type SeriesResult } from "./data";

export type Decision = "allowed" | "limited" | "refused";
export type StepState = "pass" | "warn" | "limit" | "block" | "skip";

/** Reason codes produced by the rolling-origin backtest stage (docs/MODEL_POLICY.md#reason-codes); all others are data checks. */
export const BACKTEST_CODES = [
  "insufficient_backtest", "short_backtest_span", "backtest_worse_than_baseline", "marginal_skill",
  "skill_horizon_too_short", "no_skill_beyond_persistence", "interval_undercoverage"
] as const;
export const DATA_CODES = [
  "kind_event_not_forecast", "negative_values", "too_short", "short_history", "too_many_gaps", "long_gap_at_end",
  "stale_data", "constant_series", "mostly_zeros", "mostly_zeros_warn", "zero_runs",
  "no_seasonal_signal_but_seasonal_expected", "trend_nonstationary", "structural_break", "unstable_variance",
  "outlier_contamination", "reporting_delay_tail_incomplete"
] as const;
const SEV = ["warn", "limit", "block"] as const;

export interface Run {
  id: string;
  res: SeriesResult;
  decision: Decision;
  forecastAllowed: boolean;
  reason: string;
  reasonCodes: string[];
  data: { state: StepState; reasons: GateReason[] };
  backtest: { state: StepState; reasons: GateReason[] };
  /** backtest numbers when a candidate model was chosen */
  skill: null | { model: string; rel: number; maxRel: number; cov80: number | null };
  /** step ② numbers, read or computed from the series JSON */
  overview: Overview;
  /** step ③: every model that went through the rolling backtest (non-baseline by MAE, baseline last); empty without a backtest */
  methods: Method[];
  /** recommended method = backtest.chosen_model (asserted to be the lowest-MAE candidate); null when nothing may be forecast */
  rec: string | null;
}
/** Step ③ card state: rec = recommended (selectable), alt = applicable but not recommended (amber reason, demo mode
 *  keeps the recommendation), na = not applicable to this series (red reason, disabled). */
export type MethodState = "rec" | "alt" | "na";
/** One reason, rendered by the component as i18n key `wf.why.<k>` with params, or (k = "msg") the recorded gate message. */
export interface Why { k: string; p?: Record<string, number>; code?: string }
export interface Method {
  id: string; mae: number | null; rel: number | null; baseline: boolean; recommended: boolean; beatsBaseline: boolean;
  state: MethodState; why: Why[];
}
/** Production model set of src/ailab/forecast.py (BASELINE + CANDIDATES); the build fails if backtest.models differs. */
export const PROD_MODELS = ["MSTL", "AutoTheta", "Naive", "SeasonalNaive"] as const;
/** Imported in src/ailab/forecast.py but deliberately not a candidate: "statsforecast does not fit a seasonal ETS at weekly
 *  period 52, so it collapses to Naive" (forecast.py docstring, docs/METHODS.md). */
export const EXCLUDED_ETS = "AutoETS";
/** Tried in benchmarks/forecast_bench.py, not adopted (docs/BENCHMARK.md §2): series-independent, listed once. */
export const BENCH_ONLY = ["SWA3", "MSTL_ARIMA", "ENS_mstl_theta", "ENS_mstl_theta_swa"] as const;
export interface Overview {
  from: string; to: string; n: number; seasons: number; missing: number; missingFrac: number; longestGap: number;
  last: { ds: string; y: number } | null; year: { min: number; max: number } | null;
  seasonal: number | null; seasonalCut: number | null;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const worst = (rs: GateReason[]): StepState => {
  const i = Math.max(-1, ...rs.map((r) => SEV.indexOf(r.severity as (typeof SEV)[number])));
  return i < 0 ? "pass" : SEV[i];
};

export function assertRun(id: string, res: SeriesResult): Run {
  const fail = (msg: string): never => {
    throw new Error(`[workflow ${id}] ${msg}`);
  };
  const entry = manifest().series.find((s) => s.series_id === id) ?? fail("missing from manifest.json");
  const p = res.policy as SeriesResult["policy"] & { gate_decision?: string; reason_codes?: string[]; limited?: boolean };
  const g = res.gate as SeriesResult["gate"] & { thresholds?: Record<string, number> };
  // 1. series/<id>.json is internally consistent
  if (res.series.series_id !== id) fail(`series.series_id ${res.series.series_id}`);
  if (p.gate_decision !== undefined && p.gate_decision !== g.decision) fail(`policy.gate_decision ${p.gate_decision} != gate.decision ${g.decision}`);
  if (p.reason_codes !== undefined && !same(p.reason_codes, g.reason_codes)) fail("policy.reason_codes != gate.reason_codes");
  if (p.limited !== undefined && p.limited !== (g.decision === "limited")) fail("policy.limited disagrees with gate.decision");
  if (p.forecast_allowed !== (g.decision !== "refused")) fail("forecast_allowed disagrees with gate.decision");
  if (!same(g.reasons.map((r) => r.code), g.reason_codes)) fail("gate.reasons codes != gate.reason_codes");
  if (p.forecast_allowed !== (res.forecast !== null)) fail("forecast presence disagrees with forecast_allowed");
  // 2. manifest.json agrees with series/<id>.json
  const m = entry as typeof entry & { policy?: Record<string, unknown>; gate: { decision: string; reason_codes?: string[] } };
  if (m.gate.decision !== g.decision) fail(`manifest gate.decision ${m.gate.decision} != ${g.decision}`);
  if (m.gate.reason_codes && !same(m.gate.reason_codes, g.reason_codes)) fail("manifest gate.reason_codes differ");
  if (m.policy) for (const k of ["forecast_allowed", "reason", "display_mode", "model_family", "horizon_weeks"] as const)
    if (!same(m.policy[k], p[k])) fail(`manifest policy.${k} differs`);
  // 3. the steps can be derived from the recorded reasons, and they lead to the recorded decision
  for (const r of g.reasons) {
    if (!(SEV as readonly string[]).includes(r.severity)) fail(`unknown severity ${r.severity} for ${r.code}`);
    if (![...DATA_CODES, ...BACKTEST_CODES].includes(r.code as never)) fail(`unknown reason code ${r.code} (update workflow.ts)`);
  }
  const dataR = g.reasons.filter((r) => (DATA_CODES as readonly string[]).includes(r.code));
  const btR = g.reasons.filter((r) => (BACKTEST_CODES as readonly string[]).includes(r.code));
  const dataState = worst(dataR);
  const ran = res.backtest !== null;
  if (ran === (dataState === "block")) fail(ran ? "backtest ran although a data check blocked" : "no backtest although no data check blocked");
  const btState: StepState = ran ? worst(btR) : "skip";
  const all = worst(g.reasons);
  const expected: Decision = all === "block" ? "refused" : all === "limit" ? "limited" : "allowed";
  if (expected !== g.decision) fail(`reasons imply ${expected} but gate.decision is ${g.decision}`);
  let skill: Run["skill"] = null;
  const bt = res.backtest;
  if (bt && bt.chosen_model) {
    const rel = bt.models[bt.chosen_model]?.rel_mae_vs_seasonal_naive;
    const maxRel = g.thresholds?.max_rel_mae;
    if (rel == null || maxRel == null) fail("chosen model without rel_mae_vs_seasonal_naive / max_rel_mae");
    skill = { model: bt.chosen_model, rel: rel as number, maxRel: maxRel as number, cov80: res.forecast?.calibration?.["80"]?.empirical_loo ?? null };
  }
  if (g.decision !== "refused" && !skill) fail("forecast allowed without a chosen backtest model");
  const q = res.quality;
  // 4. step ③: every method the repository has, with a state and reasons derived from this series' JSON.
  //    The recommendation is the recorded chosen model, and it must really be the lowest-MAE candidate.
  const btx = bt as (NonNullable<typeof bt> & { baseline?: string }) | null;
  const sx = res.series as SeriesResult["series"] & { season_length?: number };
  const L = sx.season_length ?? fail("series.season_length missing");
  const kind = res.series.indicator_type;
  const met = (r: GateReason, k: string) => (typeof r.metrics?.[k] === "number" ? r.metrics[k] : fail(`${r.code}.metrics.${k} missing`));
  const thr = (r: GateReason & { thresholds?: Record<string, number> }, k: string) =>
    typeof r.thresholds?.[k] === "number" ? r.thresholds[k] : fail(`${r.code}.thresholds.${k} missing`);
  const dataWhy: Why[] = dataR.filter((r) => r.severity === "block").map((r): Why =>
    r.code === "too_short" ? { k: "short", p: { n: met(r, "n_periods"), s: met(r, "seasons"), min: met(r, "min_seasons") } }
    : r.code === "too_many_gaps" ? { k: "gaps", p: { p: met(r, "missing_pct"), g: met(r, "longest_gap"), max: met(r, "max_gap") } }
    : { k: "msg", code: r.code });
  const btWhy: Why[] = btR.filter((r) => r.severity === "block").map((r): Why => {
    if (r.code !== "insufficient_backtest") return { k: "msg", code: r.code };
    const s = met(r, "seasons_covered"), min = thr(r, "backtest_seasons_block");
    return s < min ? { k: "btSeasons", p: { s, min } } : { k: "btWindows", p: { n: met(r, "n_windows"), min: met(r, "min_windows") } };
  });
  let order: string[] = [...PROD_MODELS];
  const base = btx ? (btx.baseline ?? fail("backtest without baseline")) : "SeasonalNaive";
  if (btx) {
    const ms = Object.entries(btx.models);
    if (!same(ms.map(([m]) => m).sort(), [...PROD_MODELS].sort())) fail(`backtest.models ${ms.map(([m]) => m)} != ${PROD_MODELS} (update workflow.ts)`);
    for (const [m, v] of ms) if (typeof v.mae !== "number" || typeof v.rel_mae_vs_seasonal_naive !== "number") fail(`backtest.models.${m} lacks mae / rel_mae`);
    const cands = ms.filter(([m]) => m !== base).sort((a, b) => a[1].mae - b[1].mae);
    if (btx.chosen_model && btx.chosen_model !== cands[0][0]) fail(`chosen_model ${btx.chosen_model} is not the lowest-MAE candidate ${cands[0][0]}`);
    if (res.forecast && res.forecast.model !== btx.chosen_model) fail(`forecast.model ${res.forecast.model} != chosen_model`);
    order = [...cands.map(([m]) => m), base];
  }
  const maxRel = g.thresholds?.max_rel_mae ?? 1;
  const recId = g.decision !== "refused" && btx ? btx.chosen_model : null;
  const methods: Method[] = order.map((m) => {
    const v = btx?.models[m];
    const rel = v ? (v.rel_mae_vs_seasonal_naive as number) : null;
    const st: MethodState = m === recId ? "rec" : g.decision === "refused" || kind === "event" ? "na" : "alt";
    const why: Why[] = kind === "event" ? [{ k: "event" }]
      : st !== "na" ? []
      : dataState === "block" ? [...(m === "SeasonalNaive" && q.n_weeks < L ? [{ k: "season1", p: { L } }] : []), ...dataWhy]
      : btWhy;
    if (st === "na" && !why.length) fail(`${m}: not applicable without a recorded reason`);
    return { id: m, mae: v?.mae ?? null, rel, baseline: m === base, recommended: st === "rec", beatsBaseline: m !== base && rel !== null && rel < maxRel, state: st, why };
  });
  // seasonal ETS: excluded by the code for weekly season 52; for any other season length we have no documented reason
  methods.push({ id: EXCLUDED_ETS, mae: null, rel: null, baseline: false, recommended: false, beatsBaseline: false, state: "na",
    why: [L === 52 ? { k: "ets", p: { L } } : { k: "notin" }] });
  const rec = methods.find((m) => m.recommended)?.id ?? null;
  if ((rec !== null) !== p.forecast_allowed) fail("a recommendation exists iff the forecast is allowed");
  // 5. step ②: overview numbers
  const gm = (res.gate as { metrics?: Record<string, unknown> }).metrics ?? {};
  const obs = res.observations;
  const lastI = obs.map((o) => o.y).findLastIndex((y) => y != null);
  const last = lastI < 0 ? null : { ds: obs[lastI].ds, y: obs[lastI].y as number };
  if (last && last.ds !== q.data_through) fail(`last observation ${last.ds} != quality.data_through`);
  const yr = obs.slice(-52).map((o) => o.y).filter((y): y is number => y != null);
  const num = (k: string) => (typeof gm[k] === "number" ? (gm[k] as number) : null);
  const overview: Overview = {
    from: q.data_from, to: q.data_through, n: q.n_weeks, seasons: num("seasons") ?? q.n_weeks / 52, missing: q.n_missing,
    missingFrac: num("missing_frac") ?? q.n_missing / q.n_weeks, longestGap: num("longest_gap") ?? 0, last,
    year: yr.length ? { min: Math.min(...yr), max: Math.max(...yr) } : null,
    seasonal: num("seasonal_strength"), seasonalCut: g.thresholds?.min_seasonal_strength ?? null
  };
  if (obs.length !== q.n_weeks || obs.filter((o) => o.y == null).length !== q.n_missing) fail("observations disagree with quality.n_weeks / n_missing");
  return {
    id, res, decision: g.decision, forecastAllowed: p.forecast_allowed, reason: p.reason, reasonCodes: [...g.reason_codes],
    data: { state: dataState, reasons: dataR }, backtest: { state: btState, reasons: btR }, skill, overview, methods, rec
  };
}

/** One run per showcased series, in display order. The demo must offer at least one allowed and one refused outcome. */
export function runs(): Run[] {
  const out = SERIES_IDS.map((id) => assertRun(id, series(id)));
  for (const d of ["allowed", "refused"] as const)
    if (!out.some((r) => r.decision === d)) throw new Error(`[workflow] no ${d} series left to demonstrate`);
  return out;
}
