import assert from "node:assert/strict";
import { test } from "node:test";

import { knownSearchDoor, SEARCH_DOORS, TolerantAgentCatalogSchema } from "../src/agents.ts";

const CATALOG = {
  models: [],
  support_followup: "",
  truncated: false,
  doors: { web_search: "open", find_contacts: "open" },
  search_provider: "searxng",
};

test("an unknown search door parses and reads as null; a known one passes through", () => {
  // The tolerant half: the WHOLE catalog must survive a door this
  // bundle predates (a strict enum would fail the parse and brick the
  // model picker behind an unloadable catalog).
  const parsed = TolerantAgentCatalogSchema.parse(CATALOG);
  assert.equal(parsed.search_provider, "searxng");
  // The mapping half: unknown reads as null, the value that promises
  // least; known doors pass through typed.
  assert.equal(knownSearchDoor(parsed.search_provider), null);
  assert.equal(knownSearchDoor(null), null);
  assert.equal(knownSearchDoor("duckduckgo"), "duckduckgo");
  assert.equal(knownSearchDoor("dataforseo"), "dataforseo");
});

test("the known-door set comes off the contract, never hand-retyped", () => {
  assert.deepEqual([...SEARCH_DOORS].sort(), ["dataforseo", "duckduckgo"]);
});
