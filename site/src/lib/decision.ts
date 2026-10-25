// Build-time model of the two "decision" cards shown right after the hero: one allowed and one refused real series.
// Everything is read from site/public/data (never hard-coded); assertDecision() fails the build when the static
// artefacts disagree with each other or with the role a card claims, so the cards can never drift from the data.
import { manifest, series, type SeriesResult } from "./data";

export type Decision = "allowed" | "limited" | "refused";
export interface DecisionCard {
  id: string;
  role: "allowed" | "refused";
  res: SeriesResult;
  decision: Decision;
  forecastAllowed: boolean;
  reason: string;
  reasonCodes: string[];
  displayMode: string;
  confidence: string;
}

/** The pair shown on the home page, in display order. */
export const DECISION_PAIR = [
  { id: "us_ilinet_wili", role: "allowed" },
  { id: "malta_flu_detections", role: "refused" }
] as const;

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function assertDecision(id: string, role: "allowed" | "refused", res: SeriesResult): DecisionCard {
  const fail = (msg: string): never => {
    throw new Error(`[decision-card ${id}] ${msg}`);
  };
  const entry = manifest().series.find((s) => s.series_id === id) ?? fail("missing from manifest.json");
  const p = res.policy as SeriesResult["policy"] & { gate_decision?: string; reason_codes?: string[]; limited?: boolean };
  const g = res.gate;
  // 1. series/<id>.json is internally consistent
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
  // 3. the card's role matches the data (limited is never shown as refused, nor as allowed)
  if (g.decision !== role) fail(`card role ${role} but gate.decision is ${g.decision}`);
  return {
    id, role, res, decision: g.decision, forecastAllowed: p.forecast_allowed, reason: p.reason,
    reasonCodes: [...g.reason_codes], displayMode: p.display_mode, confidence: g.confidence
  };
}

export const decisionCards = (): DecisionCard[] => DECISION_PAIR.map(({ id, role }) => assertDecision(id, role, series(id)));

/** The series shown as the third outcome in the note under the pair (first "limited" series, if any). */
export const firstLimited = (ids: readonly string[]): string | undefined => ids.find((id) => series(id).gate.decision === "limited");
