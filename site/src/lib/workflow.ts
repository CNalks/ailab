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
  return {
    id, res, decision: g.decision, forecastAllowed: p.forecast_allowed, reason: p.reason, reasonCodes: [...g.reason_codes],
    data: { state: dataState, reasons: dataR }, backtest: { state: btState, reasons: btR }, skill
  };
}

/** One run per showcased series, in display order. The demo must offer at least one allowed and one refused outcome. */
export function runs(): Run[] {
  const out = SERIES_IDS.map((id) => assertRun(id, series(id)));
  for (const d of ["allowed", "refused"] as const)
    if (!out.some((r) => r.decision === d)) throw new Error(`[workflow] no ${d} series left to demonstrate`);
  return out;
}
