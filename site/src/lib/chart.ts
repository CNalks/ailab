// Build-time SVG geometry for one series: history (broken at missing weeks), forecast + 80/95% bands, signal lanes.
import type { SeriesResult } from "./data";

export interface ChartOpts { w: number; h: number; weeks: number; locale: string; yearStep: number }
export interface Chart {
  w: number; h: number;
  hist: string; dots: { x: number; y: number }[];
  band95: string; band80: string; fc: string; last: { x: number; y: number } | null;
  yTicks: { y: number; label: string }[]; xTicks: { x: number; label: string }[];
  lanes: { method: string; label: string; y: number; xs: number[] }[];
  plot: { l: number; r: number; t: number; b: number };
  missingXs: number[];
}

const DAY = 86400000;
const LANE_LABEL: Record<string, string> = { ears_c1: "C1", ears_c2: "C2", ears_c3: "C3", cusum: "CUSUM", farrington: "FARR" };
const ts = (d: string) => Date.parse(d + "T00:00:00Z");
const r1 = (n: number) => Math.round(n * 10) / 10;
/** Path through the points with relative "l" steps between the 0.1-rounded absolute positions (same geometry, ~35% fewer bytes). */
const rel = (pts: [number, number][]) => {
  const q = pts.map(([x, y]) => [Math.round(x * 10), Math.round(y * 10)]);
  const n = (v: number) => String(v / 10).replace(/^(-?)0\./, "$1.");
  let out = `M${n(q[0][0])},${n(q[0][1])}l`;
  for (let i = 1; i < q.length; i++) {
    const dy = q[i][1] - q[i - 1][1];
    out += (i > 1 ? " " : "") + n(q[i][0] - q[i - 1][0]) + (dy < 0 ? "" : ",") + n(dy);
  }
  return out;
};

function niceTicks(max: number, n = 4): number[] {
  const raw = max / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(v);
  return out;
}

export function buildChart(res: SeriesResult, o: ChartOpts): Chart {
  const lanes = res.alerts.slice(0, 3);
  const laneH = 9;
  const m = { l: o.w < 480 ? 38 : 46, r: 10, t: 10, b: 24 + lanes.length * laneH + (lanes.length ? 8 : 0) };
  const pw = o.w - m.l - m.r;
  const ph = o.h - m.t - m.b;

  const obs = res.observations;
  const start = Math.max(0, obs.length - o.weeks);
  const win = obs.slice(start);
  const fc = res.forecast?.points ?? [];
  const t0 = ts(win[0].ds);
  const t1 = fc.length ? ts(fc[fc.length - 1].ds) : ts(win[win.length - 1].ds);
  const X = (d: string) => m.l + ((ts(d) - t0) / (t1 - t0 || 1)) * pw;

  let ymax = 0;
  for (const p of win) if (p.y != null) ymax = Math.max(ymax, p.y);
  for (const p of fc) ymax = Math.max(ymax, p.hi95 ?? p.hi80 ?? p.point);
  ymax = ymax * 1.06 || 1;
  const ticks = niceTicks(ymax);
  const top = ticks[ticks.length - 1] >= ymax ? ticks[ticks.length - 1] : ymax;
  const Y = (v: number) => m.t + ph - (v / top) * ph;

  // history segments
  let hist = "";
  const dots: { x: number; y: number }[] = [];
  let seg: [number, number][] = [];
  const flush = () => {
    if (seg.length === 1) dots.push({ x: r1(seg[0][0]), y: r1(seg[0][1]) });
    else if (seg.length > 1) hist += rel(seg);
    seg = [];
  };
  const missingXs: number[] = [];
  for (const p of win) {
    if (p.y == null) { flush(); missingXs.push(r1(X(p.ds))); } else seg.push([X(p.ds), Y(p.y)]);
  }
  flush();

  // last observation
  let lastObs: { ds: string; y: number } | null = null;
  for (let i = win.length - 1; i >= 0; i--) if (win[i].y != null) { lastObs = { ds: win[i].ds, y: win[i].y as number }; break; }
  const last = lastObs ? { x: r1(X(lastObs.ds)), y: r1(Y(lastObs.y)) } : null;

  // forecast band polygons + centre line (starts at the last observation)
  const poly = (lo: "lo80" | "lo95", hi: "hi80" | "hi95") => {
    const pts = fc.filter((p) => p[lo] != null && p[hi] != null);
    if (!pts.length || !lastObs) return "";
    const up = pts.map((p) => `${r1(X(p.ds))},${r1(Y(p[hi] as number))}`);
    const dn = pts.map((p) => `${r1(X(p.ds))},${r1(Y(p[lo] as number))}`).reverse();
    return `M${r1(X(lastObs.ds))},${r1(Y(lastObs.y))}L${up.join("L")}L${dn.join("L")}Z`;
  };
  const fcLine = fc.length && lastObs ? `M${r1(X(lastObs.ds))},${r1(Y(lastObs.y))}L` + fc.map((p) => `${r1(X(p.ds))},${r1(Y(p.point))}`).join("L") : "";

  // axes
  const nf = new Intl.NumberFormat(o.locale, { notation: "compact", maximumFractionDigits: 1 });
  const suffix = res.series.indicator_type === "proportion" ? "%" : "";
  const yTicks = ticks.map((v) => ({ y: r1(Y(v)), label: nf.format(v) + suffix }));
  const xTicks: { x: number; label: string }[] = [];
  const y0 = new Date(t0).getUTCFullYear();
  const y1 = new Date(t1).getUTCFullYear();
  for (let y = y0 + 1; y <= y1; y++) {
    const x = X(`${y}-01-01`);
    if (x > m.l + 14 && x < o.w - 14 && (y - y0) % o.yearStep === 0) xTicks.push({ x: r1(x), label: String(y) });
  }
  if (xTicks.length < 2) {
    xTicks.length = 0;
    // short series: label the first and last month
    const fmt = new Intl.DateTimeFormat(o.locale, { month: "short", year: "2-digit", timeZone: "UTC" });
    xTicks.push({ x: m.l + 20, label: fmt.format(t0) }, { x: o.w - m.r - 24, label: fmt.format(t1) });
  }

  // signal lanes (inside the window only)
  const laneOut = lanes.map((a, i) => ({
    method: a.method,
    label: LANE_LABEL[a.method] ?? a.method,
    y: r1(m.t + ph + 28 + i * laneH),
    xs: a.signal_periods.filter((d) => ts(d) >= t0 && ts(d) <= ts(win[win.length - 1].ds)).map((d) => r1(X(d)))
  }));

  return {
    w: o.w, h: o.h, hist, dots, band95: poly("lo95", "hi95"), band80: poly("lo80", "hi80"), fc: fcLine, last,
    yTicks, xTicks, lanes: laneOut, plot: { l: m.l, r: o.w - m.r, t: m.t, b: m.t + ph }, missingXs
  };
}
export const DAYMS = DAY;
