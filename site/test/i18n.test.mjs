// The three dictionaries carry the same keys and the same {placeholders}; en / fr text of the walkthrough and the
// evidence block contains no untranslated Chinese (neither in the dictionaries nor in the rendered pages).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const dict = (f) => {
  const src = fs.readFileSync(path.join(root, "src/i18n", f), "utf-8");
  return Object.fromEntries([...src.matchAll(/^\s*"([\w.]+)":\s*"((?:[^"\\]|\\.)*)",?\s*$/gm)].map((m) => [m[1], m[2]]));
};
const D = { "zh-cn": dict("zh-cn.ts"), en: dict("en.ts"), fr: dict("fr.ts") };
const ph = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
const CJK = /[\u3400-\u9fff\uff01-\uff5e]/;

test("same keys in zh-cn, en and fr", () => {
  const zh = Object.keys(D["zh-cn"]).sort();
  assert.ok(zh.length > 100, "parsed the dictionaries");
  for (const l of ["en", "fr"]) assert.deepEqual(Object.keys(D[l]).sort(), zh, `${l} keys`);
});

test("same placeholders per key", () => {
  for (const k of Object.keys(D["zh-cn"])) for (const l of ["en", "fr"]) assert.deepEqual(ph(D[l][k]), ph(D["zh-cn"][k]), `${l} ${k}`);
});

test("walkthrough + evidence keys are translated (no Chinese in en / fr)", () => {
  const keys = Object.keys(D["zh-cn"]).filter((k) => k.startsWith("wf.") || k.startsWith("ev."));
  assert.ok(keys.length >= 40);
  for (const k of keys) for (const l of ["en", "fr"]) {
    assert.ok(D[l][k] && !CJK.test(D[l][k]), `${l} ${k}`);
    assert.notEqual(D[l][k], D["zh-cn"][k]);
  }
});

test("rendered en / fr walkthrough has no Chinese text", () => {
  for (const f of ["en/forecast/index.html", "fr/forecast/index.html"]) {
    const html = fs.readFileSync(path.join(root, "dist", f), "utf-8");
    const sec = html.match(/<section class="wf" id="workflow"[\s\S]*?<\/section>/)[0];
    assert.ok(!CJK.test(sec.replace(/<[^>]+>/g, "")), f);
  }
});
