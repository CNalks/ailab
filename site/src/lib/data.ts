// Build-time loaders for the precomputed ailab export (public/data, written by scripts/build_showcase_data.py).
import fs from "node:fs";
import path from "node:path";

const DIR = path.resolve(process.cwd(), "public/data");

export type Msg = { en: string; zh: string; fr: string };
export interface GateReason { code: string; severity: string; message: Msg; metrics: Record<string, number> }
export interface Alert {
  rule_id: string; method: string; n_evaluated: number; n_signals: number; latest_is_signal: boolean;
  signal_periods: string[]; wording: string; status?: string;
}
export interface ForecastPoint { h: number; ds: string; point: number; lo80?: number; hi80?: number; lo95?: number; hi95?: number }
export interface SeriesResult {
  series: {
    series_id: string; name: string; indicator_type: "count" | "proportion" | "rate" | "event"; value_scale: number;
    unit: string; source_id: string; geography: string; licence: string; attribution: string; notice: string; retrieved_at: string;
  };
  data_through: string;
  quality: { grade: string; n_weeks: number; n_missing: number; data_from: string; data_through: string };
  gate: {
    decision: "allowed" | "limited" | "refused"; confidence: string; reasons: GateReason[]; reason_codes: string[];
    limits: { max_horizon_weeks: number | null; interval_inflation: number; baseline_only: boolean; point_only: boolean };
    recommended_action: { code: string; message: Msg };
  };
  policy: { forecast_allowed: boolean; reason: string; display_mode: string; model_family: string; horizon_weeks: number };
  backtest: null | {
    n_windows: number; seasons_covered: number; chosen_model: string;
    models: Record<string, { mae: number; rel_mae_vs_seasonal_naive?: number }>;
  };
  forecast: null | { origin: string; model: string; points: ForecastPoint[]; calibration: Record<string, { nominal: number; empirical_loo: number; n_windows: number }> };
  alerts: Alert[];
  observations: { ds: string; y: number | null }[];
}
export interface Manifest {
  ailab_version: string; generated_at: string;
  series: { series_id: string; gate: { decision: string }; data_through: string; gzip_bytes: number; download?: string }[];
}
export interface Source {
  source_id: string; name: string; publisher: string; url: string; docs_url: string; licence: string; licence_url: string;
  attribution: string; notice: string;
}

const read = <T,>(rel: string): T => JSON.parse(fs.readFileSync(path.join(DIR, rel), "utf-8")) as T;
export const manifest = (): Manifest => read<Manifest>("manifest.json");
export const sources = (): Source[] => read<{ sources: Source[] }>("sources.json").sources;
export const series = (id: string): SeriesResult => read<SeriesResult>(`series/${id}.json`);

/** Display order = the spectrum of gate outcomes. */
export const SERIES_IDS = [
  "us_ilinet_wili",
  "us_ilinet_ili_visits",
  "england_flu_positivity",
  "eu_eea_flu_positivity",
  "malta_flu_detections"
] as const;
