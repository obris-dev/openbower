import assert from "node:assert/strict";
import { test } from "node:test";

import { draftEquals, draftProvider, parseDraft, saveShape } from "./draft.ts";

const output = { key: "person", label: "Person", type: "text", description: "" };

test("garbage and non-objects parse to null, never a throw", () => {
  assert.equal(parseDraft(null), null);
  assert.equal(parseDraft("not json {"), null);
  assert.equal(parseDraft('"a string"'), null);
  assert.equal(parseDraft("[1,2]"), null);
});

test("fields a past version shaped differently are dropped, not crashed on", () => {
  const draft = parseDraft(
    JSON.stringify({
      label: "ok",
      prompt: 42,
      tools: { web_search: "yes" },
      outputs: [{ bogus: true }],
      testResult: { cells: "wrong" },
      testToolsOn: "true",
    }),
  );
  assert.deepEqual(draft, { label: "ok" });
});

test("a well-shaped draft survives whole", () => {
  const stored = {
    label: "Finder",
    prompt: "Find {{name}}",
    provider: "openai_compatible",
    tools: { web_search: true },
    outputs: [output],
    testRow: { name: "Acme" },
    testResult: { cells: { person: "Jane" }, evidence: [], searches: [] },
    testToolsOn: true,
  };
  // tools parse through the generated AgentTools schema, so a key a
  // past version never wrote fills with its wire default; the same
  // holds for the result's tool statuses, defaulted for results a
  // past version stored without them.
  assert.deepEqual(parseDraft(JSON.stringify(stored)), {
    ...stored,
    tools: { web_search: true, find_contacts: false },
    testResult: { ...stored.testResult, tools: {} },
  });
});

test("draftEquals ignores key order but nothing else", () => {
  const a = { label: "x", outputs: [{ key: "k", label: "K", type: "text" as const, description: "" }] };
  const b = { outputs: [{ description: "", type: "text" as const, label: "K", key: "k" }], label: "x" };
  assert.equal(draftEquals(a, b), true);
  assert.equal(draftEquals(a, { ...b, label: "y" }), false);
});

test("a retired provider value degrades to empty, never into typed state", () => {
  assert.equal(draftProvider("openai_compatible"), "openai_compatible");
  assert.equal(draftProvider("retired_provider"), "");
  assert.equal(draftProvider(undefined), "");
});

test("a draft from a bundle that omitted output keys keeps the edit", () => {
  const draft = parseDraft(JSON.stringify({ outputs: [{ label: "Person", type: "text", description: "" }] }));
  assert.deepEqual(draft?.outputs, [{ key: "", label: "Person", type: "text", description: "" }]);
});

test("saveShape trims and filters exactly like the request", () => {
  const shaped = saveShape({
    label: " Finder ",
    prompt: "p ",
    outputs: [
      { key: "", label: "A", type: "text", description: "" },
      { key: "", label: "", type: "text", description: "" },
    ],
  });
  assert.equal(shaped.label, "Finder");
  assert.equal(shaped.prompt, "p");
  assert.equal(shaped.outputs?.length, 1);
});
