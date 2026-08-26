import assert from "node:assert/strict";
import { test } from "node:test";

import { applyCompletion, matchVariables, slashContext } from "./slash-completion.ts";

test("a slash opens only at the start of a word", () => {
  assert.deepEqual(slashContext("/", 1), { start: 0, query: "" });
  assert.deepEqual(slashContext("Find /co", 8), { start: 5, query: "co" });
  assert.deepEqual(slashContext("line one\n/dom", 13), { start: 9, query: "dom" });
  // Mid-word slashes are authored text, never triggers.
  assert.equal(slashContext("and/or", 6), null);
  assert.equal(slashContext("https://acme.com", 16), null);
  // The trigger is judged AT the caret, not anywhere in the text.
  assert.equal(slashContext("/co find", 8), null);
  assert.deepEqual(slashContext("/co find", 3), { start: 0, query: "co" });
  // A character that cannot continue a variable name ends the run.
  assert.equal(slashContext("Find /co.", 9), null);
});

test("matches rank prefix hits before substring hits, source order kept", () => {
  const keys = ["company", "domain", "contact_name"];
  assert.deepEqual(matchVariables(keys, ""), keys);
  assert.deepEqual(matchVariables(keys, "co"), ["company", "contact_name"]);
  assert.deepEqual(matchVariables(keys, "om"), ["company", "domain"]);
  assert.deepEqual(matchVariables(keys, "CO"), ["company", "contact_name"]);
  assert.deepEqual(matchVariables(keys, "zzz"), []);
});

test("accepting replaces the /partial run and lands the caret after the token", () => {
  const context = slashContext("Find /co", 8);
  assert.ok(context);
  const applied = applyCompletion("Find /co", context, 8, "company");
  assert.equal(applied.text, "Find {{company}}");
  assert.equal(applied.caret, "Find {{company}}".length);
  // Text after the caret survives untouched.
  const mid = slashContext("Find /co and more", 8);
  assert.ok(mid);
  const appliedMid = applyCompletion("Find /co and more", mid, 8, "company");
  assert.equal(appliedMid.text, "Find {{company}} and more");
  assert.equal(appliedMid.caret, "Find {{company}}".length);
});
