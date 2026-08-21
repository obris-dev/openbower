import assert from "node:assert/strict";
import { test } from "node:test";

import { AGENT_TOOL_KEYS } from "@bower/api";

import { AGENT_TOOLS, TOOL_ORDER } from "./tools-meta.ts";

test("the display order covers exactly the wire tool set", () => {
  assert.deepEqual(new Set(TOOL_ORDER), new Set(AGENT_TOOL_KEYS));
  for (const tool of AGENT_TOOLS) {
    assert.ok(tool.label.trim().length > 0, `${tool.key} needs display copy`);
  }
});
