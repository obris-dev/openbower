import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_TOOL_KEYS, TOOL_STATUSES } from "@bower/api";

import { AGENT_TOOLS, TOOL_ORDER } from "./tools-meta.ts";

test("the display order covers exactly the wire tool set", () => {
  assert.deepEqual(new Set(TOOL_ORDER), new Set(AGENT_TOOL_KEYS));
  for (const tool of AGENT_TOOLS) {
    assert.ok(tool.label.trim().length > 0, `${tool.key} needs display copy`);
  }
});

test("the per-tool status table keys the same tool set", () => {
  // ToolKey (the copy tables' key union) derives from TOOL_STATUSES,
  // AgentToolKey from the AgentTools shape: two derivations of one
  // set, pinned equal here so a tool present in only one cannot
  // silently lose its degraded mark.
  assert.deepEqual(new Set(Object.keys(TOOL_STATUSES)), new Set(AGENT_TOOL_KEYS));
});
