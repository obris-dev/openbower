import assert from "node:assert/strict";
import { test } from "node:test";

import { COLUMN_KEY_MAX_LENGTH } from "@bower/api";

import { deriveColumnKey, reservedColumnKey, reservedOutputKey } from "./column-key.ts";

// (label, key) pairs. THE SAME VECTORS live on the server side, in
// apps/core/sheets/tests/test_column_keys.py, against the one function
// deriveColumnKey mirrors. Changing one list without the other is the drift
// they exist to catch: a fill matches an output against a column BY
// KEY, so a derivation that disagrees judges it against a different
// column than the server does.
const VECTORS: [string, string][] = [
  ["Contact Email", "contact_email"],
  ["LinkedIn URL!", "linkedin_url"],
  ["  spaced  out  ", "spaced_out"],
  ["!!!", ""],
  ["ÜBER Größe", "ber_gr_e"],
  ["Revenue ($)", "revenue"],
  ["2024 ARR", "2024_arr"],
  ["--leading--", "leading"],
  ["Ünïcode Ñame", "n_code_ame"],
  ["MiXeD CaSe", "mixed_case"],
  // Turkish dotted capital: both runtimes lowercase it to an i plus a
  // combining dot, and the dot is not [a-z0-9], so both split it.
  ["İstanbul", "i_stanbul"],
  ["café", "caf"],
];

test("mirrors the server derivation vector for vector", () => {
  for (const [label, expected] of VECTORS) {
    assert.equal(deriveColumnKey(label), expected, label);
  }
});

test("a key clamps to the column cap", () => {
  assert.equal(deriveColumnKey("a".repeat(COLUMN_KEY_MAX_LENGTH * 2)).length, COLUMN_KEY_MAX_LENGTH);
});

test("a column refuses the answer model's keys and the route-shadowing ones; an output only the first", () => {
  assert.equal(reservedOutputKey("copy"), true);
  assert.equal(reservedOutputKey("ai"), false);
  assert.equal(reservedColumnKey("ai"), true);
  assert.equal(reservedColumnKey("webhook"), true);
  assert.equal(reservedColumnKey("title"), false);
});
