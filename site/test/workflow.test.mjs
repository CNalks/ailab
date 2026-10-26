// Post-build checks for the "forecast this series" walkthrough (run after `npm run build`: `npm test`).
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
  return [...sec.matchAll(/<article class="run[^"]*" data-series="([^"]+)" data-decision="([^"]+)"[^>]*>([\s\S]*?)<\/article>/g)].map((m) => {
    const steps = [...m[3].matchAll(/<li data-state="([^"]+)"><h4>([^<]*)<\/h4><b[^>]*>([^<]*)<\/b><div class="bd">([\s\S]*?)<\/div><\/li>/g)]
      .map((s) => ({ state: s[1], title: s[2], chip: s[3], body: s[4] }));
    return { id: m[1], decision: m[2], body: m[3], steps };
  });
}

for (const [locale, [file, mk]] of Object.entries(PAGES)) {
  const html = read(path.join(dist, file));
  const sec = section(html);
  const found = runs(sec);

  test(`${locale}: one run per real series, decisions match manifest and series/<id>.json`, () => {
    assert.deepEqual(found.map((r) => r.id).sort(), manifest.series.map((s) => s.series_id).sort());
    const opts = [...sec.matchAll(/<option value="([^"]+)"/g)].map((m) => m[1]);
    assert.deepEqual(opts, found.map((r) => r.id), "one select option per run, same order");
    assert.match(sec, /<select name="wf-s"[^>]*>/);
    assert.match(sec, /<button type="button" data-wf-run>/);
    for (const r of found) {
      const s = json(`series/${r.id}.json`);
      const m = manifest.series.find((x) => x.series_id === r.id);
      assert.equal(r.decision, s.gate.decision);
      assert.equal(r.decision, m.gate.decision);
      assert.ok(r.body.includes(`<code>forecast_allowed: ${s.policy.forecast_allowed}</code>`));
      assert.equal(String(s.policy.forecast_allowed), String(m.policy.forecast_allowed));
      assert.ok(r.body.includes(`<code>reason: ${s.policy.reason}</code>`));
      assert.equal(s.policy.reason, m.policy.reason);
      assert.ok(r.body.includes(`<code>reason_codes: [${s.gate.reason_codes.join(", ")}]</code>`));
      assert.deepEqual(m.gate.reason_codes, s.gate.reason_codes);
      assert.ok(r.body.includes(`href="#s-${r.id}"`) && html.includes(`id="s-${r.id}"`), "anchor to the full card");
      assert.ok(r.body.includes("hist-view") && r.body.includes('<svg class="chart'), `${r.id}: history chart present`);
      assert.ok(r.body.includes('class="badge"') || r.body.includes("badge after-fc"), `${r.id}: decision badge`);
      assert.match(r.body, new RegExp(`data-gate="${r.decision}"`));
      if (s.policy.forecast_allowed) assert.ok(r.body.includes("fc-view"), `${r.id}: forecast chart ready after click`);
      else assert.ok(!r.body.includes("fc-view"), `${r.id}: refused has no forecast overlay chart`);
    }
    for (const d of ["allowed", "refused"]) assert.ok(found.some((r) => r.decision === d), `a ${d} run is offered`);
  });

  test(`${locale}: basis details hold the four steps derived from the gate result`, () => {
    const sev = (rs) => (rs.some((x) => x.severity === "block") ? "block" : rs.some((x) => x.severity === "limit") ? "limit" : rs.some((x) => x.severity === "warn") ? "warn" : "pass");
    for (const r of found) {
      const s = json(`series/${r.id}.json`);
      assert.ok(r.body.includes('<details class="basis">'), `${r.id}: evidence in details`);
      assert.equal(r.steps.length, 4, `${r.id}: four steps`);
      const [load, checks, bt, dec] = r.steps;
      assert.equal(load.state, "pass");
      assert.ok(strip(load.body).includes(String(s.quality.n_weeks)), "week count shown");
      for (const g of s.gate.reasons) {
        const step = BACKTEST_CODES.has(g.code) ? bt : checks;
        const other = BACKTEST_CODES.has(g.code) ? checks : bt;
        assert.ok(step.body.includes(`<code>${g.code}</code> <i>${g.severity}</i> ${esc(g.message[mk])}`), `${r.id}: ${g.code} in its step`);
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

  test(`${locale}: forecast CTA, compact switch, accessible no-JS fallback`, () => {
    assert.match(sec, /<form class="wf-form" novalidate>/);
    assert.match(sec, /data-wf-run/);
    assert.match(sec, /data-wf-select/);
    assert.ok(!/<input type="radio"/.test(sec), "no radio gallery");
    assert.match(sec, /role="status" aria-live="polite"/);
    assert.match(sec, /data-done="/);
    assert.match(sec, /<noscript><p class="wf-note">[^<]+<\/p><\/noscript>/);
    assert.match(html, /<script>document\.documentElement\.classList\.add\("wf-js"\)<\/script>/, "class set before the runs render");
    assert.match(sec, /<details class="repro">/, "CLI reproduce is secondary");
    const css = fs.readdirSync(path.join(dist, "_astro")).filter((f) => f.endsWith(".css")).map((f) => read(path.join(dist, "_astro", f))).join("");
    assert.match(css, /\.wf-form\{display:none[;}]/);
    assert.match(css, /\.wf-js \.wf-form\{display:block\}/);
    assert.match(css, /\.wf-js \.wf \.run:not\(\.on\)\{display:none\}/);
    assert.match(css, /\.wf-js \.wf \.run:not\(\.fc-on\) \.after-fc\{display:none/);
  });

  test(`${locale}: old decision cards are gone, evidence block renamed`, () => {
    assert.ok(!html.includes('<article class="dc"') && !html.includes('id="decisions"'), "no enumerated decision cards");
    const h = html.match(/<h3 class="reveal sub-h" id="evidence"[^>]*>([^<]+)<\/h3>/);
    assert.ok(h, "evidence block kept");
    assert.equal(h[1], EV_TITLE[locale]);
    for (const old of ["主张与证据", "Claims and evidence", "Affirmations et preuves"]) assert.ok(!html.includes(old), `old title ${old} gone`);
  });
}
