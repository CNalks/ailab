// Home-page HTML budget: at most 15,000 bytes over the 15f585e build (Astro 5.18) per language. The forecast page
// (/forecast/) may not exceed the home page that still carried the walkthrough (a3e5190).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dist = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "dist");
const BUDGET = 15_000;
const MAIN = { "index.html": 137_183, "en/index.html": 137_463, "fr/index.html": 140_384 };

for (const [f, base] of Object.entries(MAIN)) {
  test(`${f}: within +${BUDGET} B of main`, () => {
    const size = fs.statSync(path.join(dist, f)).size;
    const delta = size - base;
    console.log(`${f}: ${size} B (main ${base}, +${delta}, ${BUDGET - delta} B left)`);
    assert.ok(delta <= BUDGET, `${f} is ${delta} B over main (budget ${BUDGET})`);
  });
}

const WITH_DEMO = { "forecast/index.html": 142_512, "en/forecast/index.html": 142_984, "fr/forecast/index.html": 148_298 };
for (const [f, cap] of Object.entries(WITH_DEMO)) {
  test(`${f}: not larger than the home page with the walkthrough (${cap} B)`, () => {
    const size = fs.statSync(path.join(dist, f)).size;
    console.log(`${f}: ${size} B (cap ${cap}, ${cap - size} B left)`);
    assert.ok(size <= cap, `${f} is ${size - cap} B over`);
  });
}
