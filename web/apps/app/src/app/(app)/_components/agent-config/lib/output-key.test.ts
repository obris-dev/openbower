import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_OUTPUT_KEY_MAX_LENGTH } from "@bower/api";

import { outputKey, outputsProblem } from "./output-key.ts";

// (label, key) pairs. THE SAME VECTORS live on the server side, in
// apps/core/lists/tests/test_column_keys.py, against the one function
// this file mirrors. Changing one list without the other is the drift
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
    assert.equal(outputKey({ key: "", label, type: "text", description: "" }), expected, label);
  }
});

test("an explicit key wins, and the key clamps to the column cap", () => {
  assert.equal(outputKey({ key: "person", label: "Ignored Label", type: "text", description: "" }), "person");
  assert.equal(
    outputKey({ key: "", label: "a".repeat(AGENT_OUTPUT_KEY_MAX_LENGTH * 2), type: "text", description: "" }).length,
    AGENT_OUTPUT_KEY_MAX_LENGTH,
  );
});

test("outputsProblem mirrors the server's refusals and names the row", () => {
  const row = (patch: object) => ({ key: "", label: "", type: "text" as const, description: "", ...patch });
  // Untouched rows are neither valid nor a problem.
  assert.equal(outputsProblem([row({})]), null);
  assert.equal(outputsProblem([row({ label: "Answer" })]), null);
  // A description-only row must block with its cause, never drop.
  assert.match(outputsProblem([row({ description: "who to call" })])?.message ?? "", /needs a name/);
  // A label with no [a-z0-9] derives an empty key (the server 400s).
  assert.match(outputsProblem([row({ label: "!!!" })])?.message ?? "", /letter or number/);
  // Two rows landing in one column: the SECOND row is the offender
  // the jump should land on.
  const duplicate = outputsProblem([row({ label: "The Answer" }), row({ label: "the answer!" })]);
  assert.match(duplicate?.message ?? "", /same "the_answer"/);
  assert.equal(duplicate?.index, 1);
  // Names mapping to the server's reserved keys refuse HERE, worded
  // against the label ("Copy" -> copy collides with BaseModel.copy).
  for (const label of ["Copy", "Schema", "JSON"]) {
    assert.match(outputsProblem([row({ label })])?.message ?? "", new RegExp(`"${label}" can't be used`));
  }
});
