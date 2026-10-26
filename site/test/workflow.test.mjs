// Post-build checks for the four-step "run a forecast" walkthrough (run after `npm run build`: `npm test`).
// Every run must repeat site/public/data verbatim in all three languages; nothing is mocked.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(here, "..", "dist");
const data = path.join(here, "..", "public", "data");
const read = (p) => fs.readFileSync(p, "utf-8");
const json = (rel) => JSON.parse(read(path.join(data, rel)));
const manifest = json("manifest.json");
const PAGES = { "zh-cn": ["index.html", "zh"], en: ["en/index.html", "en"], fr: ["fr/index.html", "fr"] };
const EV_TITLE = { "zh-cn": "依据与边界", en: "Basis and limits", fr: "Fondements et limites" };
const BACKTEST_CODES = new Set(["insufficient_backtest", "short_backtest_span", "backtest_worse_than_baseline", "marginal_skill",
  "skill_horizon_too_short", "no_skill_beyond_persistence", "interval_undercoverage"]);
const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const strip = (s) => s.replace(/<[^>]+>/g, "");

function section(html) {
  const m = html.match(/<section class="wf" id="workflow"[\s\S]*?<\/section>/);
  assert.ok(m, "workflow section present");
  return m[0];
}
function runs(sec) {
  return [...sec.matchAll(/<article class="run[^"]*" data-series="([^"]+)" data-decision="([^"]+)" data-rec(?:="([^"]*)")?[^>]*>([\s\S]*?)<\/article>/g)].map((m) => {
    const steps = [...m[4].matchAll(/<li data-state="([^"]+)"><h4>([^<]*)<\/h4><b[^>]*>([^<]*)<\/b><div class="bd">([\s\S]*?)<\/div><\/li>/g)]
      .map((s) => ({ state: s[1], title: s[2], chip: s[3], body: s[4] }));
    const methods = [...m[4].matchAll(/<label class="mc([^"]*)"[^>]*>\s*<input type="radio" name="wf-m-[^"]+" value="([^"]+)"([^>]*)>/g)]
      .map((x) => ({ cls: x[1], id: x[2], checked: / checked\b/.test(x[3]) }));
    return { id: m[1], decision: m[2], rec: m[3] ?? "", body: m[4], steps, methods };
  });
}
const panel = (body, n) => [...body.matchAll(new RegExp(`<div class="pn p${n}[^"]*">([\\s\\S]*?)(?=<div class="pn |<div class="chartbox"|$)`, "g"))].map((m) => m[1]).join("");

for (const [locale, [file, mk]] of Object.entries(PAGES)) {
  const html = read(path.join(dist, file));
  const sec = section(html);
  const found = runs(sec);

  test(`${locale}: ① one series option and one run per real series, decisions match manifest and series/<id>.json`, () => {
    assert.deepEqual(found.map((r) => r.id).sort(), manifest.series.map((s) => s.series_id).sort());
    const opts = [...sec.matchAll(/<input type="radio" name="wf-s" value="([^"]+)"/g)].map((m) => m[1]);
    assert.deepEqual(opts, found.map((r) => r.id), "one series radio per run, same order");
    assert.equal([...sec.matchAll(/<button type="button" data-go="(\d)"/g)].map((m) => m[1]).join(""), "1234", "four-step stepper");
    for (const r of found) {
      const s = json(`series/${r.id}.json`);
      const m = manifest.series.find((x) => x.series_id === r.id);
      assert.equal(r.decision, s.gate.decision);
      assert.equal(r.decision, m.gate.decision);
      assert.ok(r.body.includes(`<code>forecast_allowed: ${s.policy.forecast_allowed}</code>`));
      assert.ok(r.body.includes(`<code>reason: ${s.policy.reason}</code>`));
      assert.ok(r.body.includes(`<code>reason_codes: [${s.gate.reason_codes.join(", ")}]</code>`));
      assert.deepEqual(m.gate.reason_codes, s.gate.reason_codes);
      assert.ok(r.body.includes(`href="#s-${r.id}"`) && html.includes(`id="s-${r.id}"`), "anchor to the full card");
      assert.equal((r.body.match(/<svg class="chart/g) || []).length, 1, `${r.id}: one chart shared by steps ② and ④`);
      assert.match(r.body, new RegExp(`class="badge" data-gate="${r.decision}"`));
      if (s.policy.forecast_allowed) assert.match(r.body, /<path class="b80"/, `${r.id}: forecast band in the chart`);
      else assert.ok(!/<path class="(fc|b80|b95)"/.test(r.body), `${r.id}: refused has no forecast in the chart`);
    }
    for (const d of ["allowed", "limited", "refused"]) assert.ok(found.some((r) => r.decision === d), `a ${d} run is offered`);
  });

  test(`${locale}: ② data overview is read from the data`, () => {
    for (const r of found) {
      const s = json(`series/${r.id}.json`);
      const p2 = panel(r.body, 2);
      assert.equal((p2.match(/<dt>/g) || []).length, 7, `${r.id}: seven overview facts`);
      assert.ok(strip(p2).includes(String(s.quality.n_weeks)), `${r.id}: length`);
      const dataReasons = s.gate.reasons.filter((g) => !BACKTEST_CODES.has(g.code));
      for (const g of dataReasons) assert.ok(p2.includes(esc(g.message[mk])), `${r.id}: ${g.code} explained in plain words`);
      if (!dataReasons.length) assert.match(p2, /class="ok"/);
      assert.match(p2, /data-to="3"/, "next goes to step ③");
    }
  });

  test(`${locale}: ③ methods are the backtested models; the recommendation is the lowest-MAE chosen model`, () => {
    for (const r of found) {
      const s = json(`series/${r.id}.json`);
      const bt = s.backtest;
      if (!bt) {
        assert.equal(r.methods.length, 0, `${r.id}: no backtest -> no methods`);
        assert.equal(r.rec, "");
        continue;
      }
      const base = bt.baseline;
      const order = Object.entries(bt.models).filter(([k]) => k !== base).sort((a, b) => a[1].mae - b[1].mae).map(([k]) => k);
      assert.deepEqual(r.methods.map((x) => x.id), [...order, base], `${r.id}: every backtested model, baseline last`);
      if (s.policy.forecast_allowed) {
        assert.equal(r.rec, bt.chosen_model);
        assert.equal(order[0], bt.chosen_model, "chosen = lowest MAE");
        assert.equal(s.forecast.model, bt.chosen_model);
        const rec = r.methods.filter((x) => x.checked);
        assert.deepEqual(rec.map((x) => x.id), [bt.chosen_model], "recommended method selected by default");
        assert.match(r.methods.find((x) => x.id === bt.chosen_model).cls, /\brec\b/);
        assert.ok(!/<fieldset class="ms" disabled/.test(r.body));
      } else {
        assert.equal(r.rec, "");
        assert.ok(r.methods.every((x) => !x.checked && !/\brec\b/.test(x.cls)), `${r.id}: nothing recommended`);
        assert.match(r.body, /<fieldset class="ms" disabled/);
      }
      if (s.gate.decision !== "allowed") assert.match(panel(r.body, 3), new RegExp(`class="mn" data-dec="${s.gate.decision}"`), `${r.id}: reason shown`);
    }
  });

  test(`${locale}: ④ result — decision, forecast points, basis and limits with the four recorded steps`, () => {
    const sev = (rs) => (rs.some((x) => x.severity === "block") ? "block" : rs.some((x) => x.severity === "limit") ? "limit" : rs.some((x) => x.severity === "warn") ? "warn" : "pass");
    for (const r of found) {
      const s = json(`series/${r.id}.json`);
      const p4 = panel(r.body, 4);
      if (s.forecast) assert.equal((p4.match(/<ul class="pts fcl">([\s\S]*?)<\/ul>/)[1].match(/<li>/g) || []).length, Math.min(2, s.forecast.points.length));
      else for (const g of s.gate.reasons.filter((x) => x.severity === "block")) assert.ok(p4.includes(esc(g.message[mk])), `${r.id}: refusal reason ${g.code}`);
      assert.match(r.body, new RegExp(`<details class="basis">\\s*<summary>${EV_TITLE[locale]}</summary>`), `${r.id}: collapsed “${EV_TITLE[locale]}”`);
      assert.equal(r.steps.length, 4, `${r.id}: four steps`);
      const [load, checks, bt, dec] = r.steps;
      assert.equal(load.state, "pass");
      assert.ok(strip(load.body).includes(String(s.quality.n_weeks)), "week count shown");
      for (const g of s.gate.reasons) {
        const step = BACKTEST_CODES.has(g.code) ? bt : checks;
        const other = BACKTEST_CODES.has(g.code) ? checks : bt;
        assert.ok(step.body.includes(`<code>${g.code}</code> <i>${g.severity}</i>`), `${r.id}: ${g.code} in its step`);
        assert.ok(!other.body.includes(`<code>${g.code}</code>`), `${r.id}: ${g.code} only once`);
      }
      assert.equal(checks.state, sev(s.gate.reasons.filter((g) => !BACKTEST_CODES.has(g.code))));
      if (s.backtest === null) assert.equal(bt.state, "skip", `${r.id}: no backtest -> skipped step`);
      else {
        assert.equal(bt.state, sev(s.gate.reasons.filter((g) => BACKTEST_CODES.has(g.code))));
        assert.ok(strip(bt.body).includes(String(s.backtest.n_windows)), "backtest origins shown");
        if (s.backtest.chosen_model) assert.ok(bt.body.includes(s.backtest.chosen_model), "chosen model shown");
        else assert.ok(!/MAE/.test(bt.body), "no skill numbers without a chosen model");
      }
      assert.equal(dec.state, s.gate.decision);
    }
  });

  test(`${locale}: step navigation, accessible no-JS fallback, CLI secondary`, () => {
    assert.match(sec, /data-step="1"/);
    assert.match(sec, /data-use-rec/);
    assert.match(sec, /role="status" aria-live="polite"/);
    assert.match(sec, /data-done="/);
    assert.match(sec, /<noscript><p class="wf-note">[^<]+<\/p><\/noscript>/);
    assert.match(html, /<script>document\.documentElement\.classList\.add\("wf-js"\)<\/script>/, "class set before the runs render");
    assert.match(sec, /<details class="repro">/, "CLI reproduce is secondary");
    if (locale === "zh-cn") assert.ok(!strip(sec).includes("闸门"), "no 闸门 in the walkthrough");
    const css = fs.readdirSync(path.join(dist, "_astro")).filter((f) => f.endsWith(".css")).map((f) => read(path.join(dist, "_astro", f))).join("");
    assert.match(css, /html:not\(\.wf-js\) \.wf \.js\{display:none\}/);
    assert.match(css, /\.wf-js \.wf \.run:not\(\.on\)\{display:none\}/);
    assert.match(css, /\.wf-js \.wf\[data-step="1"\] \.p1/);
    assert.match(css, /\.wf-js \.wf\[data-step="4"\] \.p4\{display:block\}/);
  });

  test(`${locale}: old decision cards are gone, evidence block renamed`, () => {
    assert.ok(!html.includes('<article class="dc"') && !html.includes('id="decisions"'), "no enumerated decision cards");
    const h = html.match(/<h3 class="reveal sub-h" id="evidence"[^>]*>([^<]+)<\/h3>/);
    assert.ok(h, "evidence block kept");
    assert.equal(h[1], EV_TITLE[locale]);
    for (const old of ["主张与证据", "Claims and evidence", "Affirmations et preuves"]) assert.ok(!html.includes(old), `old title ${old} gone`);
  });
}
