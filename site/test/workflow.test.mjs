// Post-build checks for the four-step "run a forecast" walkthrough on /forecast/ (run after `npm run build`: `npm test`)
// and for the home page that links to it.
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
const PAGES = { "zh-cn": ["forecast/index.html", "zh"], en: ["en/forecast/index.html", "en"], fr: ["fr/forecast/index.html", "fr"] };
const HOME = { "zh-cn": ["index.html", "/ai4health/lab/"], en: ["en/index.html", "/ai4health/lab/en/"], fr: ["fr/index.html", "/ai4health/lab/fr/"] };
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
  return [...sec.matchAll(/<article class="run[^"]*" data-series="([^"]+)" data-decision="([^"]+)" data-rec(?:="([^"]*)")?([^>]*)>([\s\S]*?)<\/article>/g)].map((m) => {
    const steps = [...m[5].matchAll(/<li data-state="([^"]+)"><h4>([^<]*)<\/h4><b[^>]*>([^<]*)<\/b><div class="bd">([\s\S]*?)<\/div><\/li>/g)]
      .map((s) => ({ state: s[1], title: s[2], chip: s[3], body: s[4] }));
    const methods = [...m[5].matchAll(/<label class="mc ([^"]*)"[^>]*>\s*<input type="radio" name="wf-m-[^"]+" value="([^"]+)"([^>]*)>([\s\S]*?)<\/label>/g)]
      .map((x) => ({ state: x[1].split(" ")[0], id: x[2], checked: / checked\b/.test(x[3]), disabled: / disabled\b/.test(x[3]), aria: /aria-disabled="true"/.test(x[3]), why: (x[4].match(/<span class="mw">([^<]*)<\/span>/) || [])[1] || "" }));
    return { id: m[1], decision: m[2], rec: m[3] ?? "", stop: / data-stop\b/.test(m[4]), body: m[5], steps, methods };
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
      assert.ok(!r.body.includes(`href="#s-`), "no link to the removed series cards");
      assert.ok(r.body.includes(`href="/ai4health/lab/data/series/${r.id}.json"`), `${r.id}: JSON download in step ②`);
      if (m.download) assert.ok(panel(r.body, 2).includes(`href="/ai4health/lab/data/${m.download}"`), `${r.id}: Parquet download in step ②`);
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
      if (r.stop) {
        // no selectable method -> stopped here: red conclusion, basis, "pick another series"; no step ③/④ at all
        assert.ok(!/data-to="3"/.test(p2) && /<button type="button" class="pri" data-to="1">/.test(p2), `${r.id}: only "pick another series"`);
        assert.match(p2, /<div class="stop"><h5>[^<]+<\/h5><ul>(<li>[^<]+<\/li>)+<\/ul><\/div>/, `${r.id}: red conclusion with reasons`);
        assert.match(p2, /<details class="basis">/, `${r.id}: basis under the conclusion`);
        assert.equal(panel(r.body, 3) + panel(r.body, 4), "", `${r.id}: no step ③/④ markup`);
      } else assert.match(p2, /data-to="3"/, "next goes to step ③");
    }
    // stopped = no selectable method, derived from the data: exactly the refused series here
    for (const r of found) assert.equal(r.stop, !json(`series/${r.id}.json`).policy.forecast_allowed, `${r.id}: stop <=> refused`);
    const why = (id) => panel(found.find((r) => r.id === id).body, 2).match(/<div class="stop">([\s\S]*?)<\/div>/)[1];
    assert.notEqual(why("eu_eea_flu_positivity"), why("malta_flu_detections"), "EU (thin backtest) and Malta (data blocked) give different reasons");
    assert.ok(strip(why("malta_flu_detections")).includes("51"), "Malta reason quotes its 51 weeks");
    assert.ok(!found.some((r) => !r.stop && /class="stop"/.test(r.body)), "no conclusion block on forecastable series");
  });

  test(`${locale}: ③ every repository method is listed; recommended / amber (demo) / red (unavailable) follow the data`, () => {
    const PROD = ["MSTL", "AutoTheta", "Naive", "SeasonalNaive"];
    for (const r of found.filter((r) => !r.stop)) {
      const s = json(`series/${r.id}.json`);
      const bt = s.backtest;
      const order = bt
        ? [...Object.entries(bt.models).filter(([k]) => k !== bt.baseline).sort((a, b) => a[1].mae - b[1].mae).map(([k]) => k), bt.baseline]
        : PROD;
      if (bt) assert.deepEqual(Object.keys(bt.models).sort(), [...PROD].sort(), "backtest models = production set");
      assert.deepEqual(r.methods.map((x) => x.id), [...order, "AutoETS"], `${r.id}: production models (by MAE, baseline last) + excluded seasonal ETS`);
      const ets = r.methods.find((x) => x.id === "AutoETS");
      assert.ok(ets.state === "na" && ets.disabled && ets.why, `${r.id}: seasonal ETS red with a reason`);
      assert.equal(s.series.season_length, 52, "the ETS reason is the weekly-52 limitation");
      for (const x of r.methods) assert.ok(x.why.length > 0, `${r.id}/${x.id}: every card has a reason`);
      assert.ok(s.policy.forecast_allowed);
      {
        assert.equal(r.rec, bt.chosen_model);
        assert.equal(order[0], bt.chosen_model, "chosen = lowest MAE");
        assert.equal(s.forecast.model, bt.chosen_model);
        const rec = r.methods.find((x) => x.id === bt.chosen_model);
        assert.ok(rec.state === "rec" && rec.checked && !rec.disabled && !rec.aria, "recommended: selected, selectable");
        for (const x of r.methods.filter((x) => PROD.includes(x.id) && x.id !== bt.chosen_model))
          assert.ok(x.state === "alt" && x.aria && !x.disabled && !x.checked, `${r.id}/${x.id}: amber, demo-locked`);
        assert.ok(r.methods.some((x) => x.state === "alt") && r.methods.some((x) => x.state === "na"), `${r.id}: has amber and red`);
      }
      // red cards are collapsed in one <details>, count in the summary, amber/recommended stay open
      const p3 = panel(r.body, 3);
      const nax = p3.match(/<details class="nax">\s*<summary>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/);
      assert.ok(nax, `${r.id}: unavailable methods collapsed`);
      const na = r.methods.filter((x) => x.state === "na");
      assert.ok(nax[1].includes(`<span data-n>${na.length}</span>`), `${r.id}: count in the summary`);
      for (const x of na) assert.ok(nax[2].includes(`value="${x.id}"`), `${r.id}/${x.id}: inside the collapsed block`);
      for (const x of r.methods.filter((x) => x.state !== "na")) assert.ok(!nax[2].includes(`value="${x.id}"`), `${r.id}/${x.id}: not collapsed`);
      assert.ok(!/class="mn" data-dec="refused"/.test(p3));
    }
    assert.match(sec, /<details class="bx nax">\s*<summary>[^<]+<\/summary>\s*<div class="nag" data-wf-x>/, "benchmark-only methods: collapsed too");
    // benchmark-only methods: one card each, disabled, numbers recomputed here from the committed raw results
    const rows = JSON.parse(read(path.join(here, "..", "..", "benchmarks", "results", "forecast.json"))).rows.filter((r) => !r.masked && r.metrics);
    const prod = new Set([...PROD]);
    const benchIds = Object.keys(rows[0].metrics).filter((m) => !prod.has(m));
    const cards = [...sec.matchAll(/<label class="mc na bench">\s*<input type="radio" name="wf-m-x" value="([^"]+)" disabled>([\s\S]*?)<\/label>/g)];
    assert.deepEqual(cards.map((c) => c[1]), benchIds, "one card per benchmark-only method");
    const gm = (x) => Math.exp(x.reduce((a, b) => a + Math.log(b), 0) / x.length);
    const fmt = (v) => new Intl.NumberFormat(mk === "zh" ? "zh-CN" : mk, { maximumFractionDigits: 2 }).format(v);
    for (const [, id, body] of cards) {
      assert.match(body, /<em>[^<]+<\/em>/, `${id}: unavailable tag`);
      const all = gm(rows.map((r) => r.metrics[id].mae / r.metrics.SeasonalNaive.mae));
      assert.ok(strip(body).includes(fmt(all)), `${id}: pooled relative MAE ${fmt(all)} shown`);
      for (const [sid, sc] of [["us_ilinet_wili", "real_wili"], ["us_ilinet_ili_visits", "real_num_ili"]]) {
        const r = rows.find((x) => x.scenario === sc);
        const line = body.match(new RegExp(`data-for="${sid}">([^<]*)<`));
        assert.ok(line && line[1].includes(fmt(r.metrics[id].mae / r.metrics.SeasonalNaive.mae)), `${id}: ${sid} figure`);
      }
    }
    for (const r of found) assert.ok(!/<label class="mc alt[^"]*">[\s\S]*?<em>[\s\S]*?<\/label>/.test(r.body.replace(/<label class="mc (rec|na)[\s\S]*?<\/label>/g, "")), `${r.id}: no tag on amber cards`);
    assert.match(sec, /<div class="wf-toast js" role="status" aria-live="assertive" data-wf-toast hidden><\/div>/);
    assert.match(sec, /data-demo="[^"]+"/);
  });

  test(`${locale}: ④ result — decision, forecast points, basis and limits with the four recorded steps`, () => {
    const sev = (rs) => (rs.some((x) => x.severity === "block") ? "block" : rs.some((x) => x.severity === "limit") ? "limit" : rs.some((x) => x.severity === "warn") ? "warn" : "pass");
    for (const r of found) {
      const s = json(`series/${r.id}.json`);
      const p4 = panel(r.body, 4);
      if (r.stop) assert.equal(p4, "", `${r.id}: no step ④`);
      else assert.equal((p4.match(/<ul class="pts fcl">([\s\S]*?)<\/ul>/)[1].match(/<li>/g) || []).length, Math.min(2, s.forecast.points.length));
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

  test(`${locale}: forecast page has an h1, breadcrumb and a way back home`, () => {
    const home = HOME[locale][1];
    assert.match(sec, /<h1 id="wf-t">[^<]+<\/h1>/);
    assert.match(html, new RegExp(`<nav class="crumb container[^"]*"[^>]*aria-label="[^"]+"[^>]*>\\s*<ol[^>]*><li[^>]*><a href="${home}"[^>]*>[^<]+</a></li><li aria-current="page"[^>]*>[^<]+</li></ol>`));
    assert.match(html, new RegExp(`<p class="back container[^"]*"[^>]*><a href="${home}"[^>]*>`));
    assert.match(html, new RegExp(`<a href="${home}forecast/" aria-current="page"`), "nav marks the forecast page");
  });

  const homeHtml = read(path.join(dist, HOME[locale][0]));
  test(`${locale}: home — walkthrough and series cards moved out, hero button goes to the forecast page, quick start after methods`, () => {
    const home = HOME[locale][1];
    assert.ok(!homeHtml.includes('id="workflow"') && !homeHtml.includes('class="wf"'), "no walkthrough on the home page");
    assert.ok(!homeHtml.includes('id="series"') && !/id="s-[\w-]+"/.test(homeHtml) && !/href="[^"]*#(series|workflow|s-[\w-]+)"/.test(homeHtml), "no series cards or links to them");
    const cta = homeHtml.match(/<div class="cta"[^>]*>([\s\S]*?)<\/div>/)[1];
    assert.match(cta, new RegExp(`<a class="btn primary press"[^>]*href="${home}forecast/"`), "primary hero button -> forecast page");
    assert.ok(!cta.includes("#quickstart"));
    const order = [...homeHtml.matchAll(/<section class="[^"]*"(?: id="(\w+)")?[^>]*aria-labelledby="([\w-]+)"/g)].map((m) => m[1] || m[2]);
    assert.deepEqual(order, ["hero-title", "what-title", "pipeline", "methods", "quickstart", "licences"]);
    const qs = homeHtml.match(/<section class="[^"]*" id="quickstart"[\s\S]*?<\/section>/)[0];
    assert.ok(!/<a class="btn|<button/.test(qs), "quick start has no buttons");
    const h1 = homeHtml.match(/<h1 id="hero-title"[^>]*>([\s\S]*?)<\/h1>/)[1];
    if (locale === "zh-cn") {
      assert.deepEqual([...h1.matchAll(/<span[^>]*>([^<]+)<\/span>/g)].map((m) => m[1]), ["未有明征休妄断，", "宁将一默胜虚辞。"], "h1 = lines 3–4");
      assert.match(homeHtml, /<p class="verse"[^>]*><span[^>]*>潮来潮去自成期，<\/span><span[^>]*>旧岁回看始自知。<\/span><\/p>/, "lines 1–2 above the h1");
    } else {
      const proverb = { en: "One swallow does not make a summer.", fr: "Une hirondelle ne fait pas le printemps." }[locale];
      assert.deepEqual([...h1.matchAll(/<span[^>]*>([^<]+)<\/span>/g)].map((m) => m[1]), [proverb], `${locale} h1 = the proverb`);
      assert.match(homeHtml, /<h1 id="hero-title" class="poem"/);
      assert.ok(!homeHtml.includes('class="verse"'), `${locale}: no lead-in`);
    }
    if (locale === "zh-cn") assert.equal((strip(homeHtml.match(/<main[\s\S]*<\/main>/)[0]).match(/闸门/g) || []).length, 5, "质量闸门 only in the 5 intro spots");
  });

  test(`${locale}: old decision cards are gone, evidence block renamed`, () => {
    const html = homeHtml;
    assert.ok(!html.includes('<article class="dc"') && !html.includes('id="decisions"'), "no enumerated decision cards");
    const h = html.match(/<h3 class="reveal sub-h" id="evidence"[^>]*>([^<]+)<\/h3>/);
    assert.ok(h, "evidence block kept");
    assert.equal(h[1], EV_TITLE[locale]);
    for (const old of ["主张与证据", "Claims and evidence", "Affirmations et preuves"]) assert.ok(!html.includes(old), `old title ${old} gone`);
  });
}
