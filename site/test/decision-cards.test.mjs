// Post-build checks for the two decision cards on the home page (run after `npm run build`: `npm test`).
// The cards must repeat site/public/data verbatim, in all three languages, and must never draw a forecast for a refusal.
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
const PAGES = { "zh-cn": "index.html", en: "en/index.html", fr: "fr/index.html" };
const PAIR = { us_ilinet_wili: "allowed", malta_flu_detections: "refused" };

// <code data-astro-cid-…>text</code> (scoped styles add an attribute)
const hasCode = (body, text) => new RegExp(`<code[^>]*>${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}</code>`).test(body);

function cards(html) {
  const out = {};
  const re = /<article class="dc"([^>]*)>([\s\S]*?)<\/article>/g;
  for (const m of html.matchAll(re)) {
    // Astro renders an empty attribute as a bare `data-x`, so a missing value means ""
    const attr = (n) => { const a = m[1].match(new RegExp(`\\sdata-${n}(?:="([^"]*)")?(?=[\\s>])`)); return a ? (a[1] ?? "") : undefined; };
    out[attr("series")] = {
      decision: attr("decision"), forecastAllowed: attr("forecast-allowed"), reason: attr("reason"),
      reasonCodes: attr("reason-codes"), body: m[2]
    };
  }
  return out;
}

for (const [locale, file] of Object.entries(PAGES)) {
  test(`${locale}: decision cards match site/public/data`, () => {
    const html = read(path.join(dist, file));
    const found = cards(html);
    assert.deepEqual(Object.keys(found), Object.keys(PAIR), "exactly the two cards, in order");
    for (const [id, role] of Object.entries(PAIR)) {
      const s = json(`series/${id}.json`);
      const m = manifest.series.find((x) => x.series_id === id);
      const c = found[id];
      assert.equal(c.decision, s.gate.decision);
      assert.equal(c.decision, m.gate.decision);
      assert.equal(c.decision, role);
      assert.equal(c.forecastAllowed, String(s.policy.forecast_allowed));
      assert.equal(c.forecastAllowed, String(m.policy.forecast_allowed));
      assert.equal(c.reason, s.policy.reason);
      assert.equal(c.reason, m.policy.reason);
      assert.equal(c.reasonCodes, s.gate.reason_codes.join(","));
      assert.equal(c.reasonCodes, m.gate.reason_codes.join(","));
      // the visible text repeats the same values
      assert.ok(hasCode(c.body, String(s.policy.forecast_allowed)), "forecast_allowed shown");
      assert.ok(hasCode(c.body, s.policy.reason), "reason shown");
      for (const code of s.gate.reason_codes) assert.ok(hasCode(c.body, code), `reason code ${code} shown`);
      // evidence anchor points at an existing full card
      assert.ok(c.body.includes(`href="#s-${id}"`));
      assert.ok(html.includes(`id="s-${id}"`));
    }
    // a refusal draws no forecast line and no band (nothing after the last observation)
    const refused = found.malta_flu_detections.body;
    assert.ok(!/class="fc"/.test(refused) && !/class="b80"/.test(refused), "refused card has no forecast geometry");
    assert.ok(/class="fc"/.test(found.us_ilinet_wili.body), "allowed card shows its forecast");
    // limited is a separate outcome: never folded into the refused card
    for (const sm of manifest.series.filter((x) => x.gate.decision === "limited"))
      assert.ok(!(sm.series_id in found), `${sm.series_id} (limited) is not shown as a decision card`);
  });
}

test("refused thumbnail: missing weeks are gaps, not zeros", () => {
  const s = json("series/malta_flu_detections.json");
  const obs = s.observations;
  assert.ok(obs.some((o) => o.y === null), "fixture still has missing weeks");
  assert.ok(obs.every((o) => o.y === null || o.y > 0), "no observed zero in the data");
  // every plotted point must correspond to an observed (non-null) week: count dots + path vertices
  const html = read(path.join(dist, PAGES["zh-cn"]));
  const body = cards(html).malta_flu_detections.body;
  const dots = (body.match(/<circle class="dot"/g) || []).length;
  const d = (body.match(/<path class="hist"[^>]*d="([^"]*)"/) || [])[1] || "";
  const vertices = d ? d.split(/[ML]/).filter(Boolean).length : 0;
  assert.equal(dots + vertices, obs.filter((o) => o.y !== null).length);
});
