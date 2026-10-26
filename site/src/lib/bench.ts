// Build-time summary of the offline forecast benchmark (benchmarks/results/forecast.json, written by
// benchmarks/forecast_bench.py; tables in docs/BENCHMARK.md §2). Used for the step ③ cards of the methods that were
// benchmarked but are not production candidates. Numbers are computed here from the committed raw results, never typed in.
import fs from "node:fs";
import path from "node:path";
import { PROD_MODELS } from "./workflow";

const FILE = path.resolve(process.cwd(), "../benchmarks/results/forecast.json");
/** forecast_bench.py's selection rule (= production): the candidate with the lowest calibration-period MAE. */
const CANDIDATES = ["MSTL", "AutoTheta", "Naive"];
/** Showcase series that are in the benchmark (same bundled FluView fixture). */
export const BENCH_SCENARIO: Record<string, string> = { us_ilinet_wili: "real_wili", us_ilinet_ili_visits: "real_num_ili" };
export const COVID = "covid_like";

type Row = { scenario: string; masked: boolean; metrics: Record<string, { mae: number; cal_mae: number }> | null };
export interface BenchMethod {
  id: string;
  /** relative MAE vs SeasonalNaive (geometric mean over seeds), pooled over all unmasked scenarios */
  all: number; covid: number;
  /** per showcase series in the benchmark */
  series: Record<string, number>;
}
export interface Bench { methods: BenchMethod[]; sel: { all: number; covid: number; series: Record<string, number> }; scenarios: number }

let cache: Bench | null = null;
export function bench(): Bench {
  if (cache) return cache;
  if (!fs.existsSync(FILE)) throw new Error(`[bench] ${FILE} missing`);
  const rows = (JSON.parse(fs.readFileSync(FILE, "utf-8")).rows as Row[]).filter((r) => !r.masked && r.metrics);
  const models = Object.keys(rows[0].metrics!);
  const ids = models.filter((m) => !(PROD_MODELS as readonly string[]).includes(m));
  for (const m of PROD_MODELS) if (!models.includes(m)) throw new Error(`[bench] production model ${m} not in the benchmark`);
  const gm = (x: number[]) => Math.exp(x.reduce((a, b) => a + Math.log(b), 0) / x.length);
  const rel = (pick: (r: Row) => string | null, keep: (r: Row) => boolean) => {
    const v = rows.filter(keep).map((r) => {
      const m = pick(r)!;
      return r.metrics![m].mae / r.metrics!.SeasonalNaive.mae;
    });
    if (!v.length) throw new Error("[bench] empty selection");
    return gm(v);
  };
  const sel = (r: Row) => CANDIDATES.reduce((a, b) => (r.metrics![b].cal_mae < r.metrics![a].cal_mae ? b : a));
  const stat = (pick: (r: Row) => string) => ({
    all: rel(pick, () => true),
    covid: rel(pick, (r) => r.scenario === COVID),
    series: Object.fromEntries(Object.entries(BENCH_SCENARIO).map(([id, sc]) => [id, rel(pick, (r) => r.scenario === sc)]))
  });
  cache = {
    methods: ids.map((id) => ({ id, ...stat(() => id) })),
    sel: stat(sel),
    scenarios: new Set(rows.map((r) => r.scenario)).size
  };
  return cache;
}
