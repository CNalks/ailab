// Home-page HTML budget: at most 15,000 bytes over the main-branch build (15f585e, Astro 5.18) per language.
// The baselines are the byte sizes of dist/{,en/,fr/}index.html built from main; update them only when main changes.
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
