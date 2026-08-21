import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_OUTPUT_KEY_MAX_LENGTH } from "@bower/api";

import { outputKey, outputsProblem } from "./output-key.ts";

test("mirrors the serializer's derivation (key wins, label slugs, clamp)", () => {
  assert.equal(outputKey({ key: "person", label: "x", type: "text", description: "" }), "person");
  assert.equal(outputKey({ key: "", label: "LinkedIn URL!", type: "url", description: "" }), "linkedin_url");
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
