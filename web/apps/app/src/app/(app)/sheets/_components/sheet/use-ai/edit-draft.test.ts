import assert from "node:assert/strict";
import { test } from "node:test";

import { configFrom, draftFrom, isDirty } from "./edit-draft.ts";

const stored = {
  prompt: "Find the founder of {{company}}",
  provider: "openai_compatible" as const,
  source: "ollama",
  model: "test-model",
  tools: { web_search: true, find_contacts: false },
  outputs: [{ key: "founder", label: "Founder", type: "text" as const, description: "Full name" }],
};

test("a fresh draft is clean, and each editable field dirties it", () => {
  const draft = draftFrom(stored);
  assert.equal(isDirty(draft, stored), false);
  assert.equal(isDirty({ ...draft, prompt: "Find the CEO of {{company}}" }, stored), true);
  assert.equal(isDirty({ ...draft, model: "other-model" }, stored), true);
  assert.equal(isDirty({ ...draft, tools: { ...draft.tools, web_search: false } }, stored), true);
  const described = draft.outputs.map((output) => ({ ...output, description: "The CEO if several" }));
  assert.equal(isDirty({ ...draft, outputs: described }, stored), true);
});

test("whitespace around the prompt is not an edit", () => {
  assert.equal(isDirty({ ...draftFrom(stored), prompt: `  ${stored.prompt}\n` }, stored), false);
});

test("a draft does not alias the stored config", () => {
  const draft = draftFrom(stored);
  draft.outputs[0]!.description = "changed";
  assert.equal(stored.outputs[0]!.description, "Full name");
});

test("a blank prompt or an unchosen model cannot be sent", () => {
  assert.equal(configFrom({ ...draftFrom(stored), prompt: "   " }), null);
  assert.equal(configFrom({ ...draftFrom(stored), provider: "" }), null);
  assert.deepEqual(configFrom(draftFrom(stored)), stored);
});
