import assert from "node:assert/strict";
import { test } from "node:test";

import { knownSearchProvider, SEARCH_PROVIDER_CHOICES, TolerantAgentCatalogSchema } from "../src/agents.ts";

const CATALOG = {
  models: [],
  support_followup: "",
  truncated: false,
  tools: { web_search: "open", find_contacts: "open" },
  search_provider: "searxng",
};

test("an unknown search provider parses and reads as null; a known one passes through", () => {
  // The tolerant half: the WHOLE catalog must survive a provider this
  // bundle predates (a strict enum would fail the parse and brick the
  // model picker behind an unloadable catalog).
  const parsed = TolerantAgentCatalogSchema.parse(CATALOG);
  assert.equal(parsed.search_provider, "searxng");
  // The mapping half: unknown reads as null, the value that promises
  // least; known doors pass through typed.
  assert.equal(knownSearchProvider(parsed.search_provider), null);
  assert.equal(knownSearchProvider(null), null);
  assert.equal(knownSearchProvider("duckduckgo"), "duckduckgo");
  assert.equal(knownSearchProvider("serper"), "serper");
});

test("the known-provider set comes off the contract, never hand-retyped", () => {
  assert.deepEqual([...SEARCH_PROVIDER_CHOICES].sort(), ["duckduckgo", "serper"]);
});
