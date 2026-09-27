import assert from "node:assert/strict";
import { test } from "node:test";

import { createdColumnKey } from "./created-columns.ts";

const plain = (key: string) => ({ key, label: key, type: "text" as const, kind: "plain" as const });
const ai = (key: string, node_id: string) => ({
  key,
  label: key,
  type: "text" as const,
  kind: "ai" as const,
  node_id,
  current_fill_id: "",
});

test("the created column is the reply's last one", () => {
  // An earlier AI column (another tab's, or an older column of the same
  // node) sits ahead of the create's own: the last is the create's.
  const columns = [plain("company"), ai("stale", "01NODEOTHER"), ai("older", "01NODENEW"), ai("email", "01NODENEW")];
  assert.equal(createdColumnKey(columns), "email");
});

test("a reply whose last column is not an AI column created none", () => {
  assert.equal(createdColumnKey([plain("company")]), null);
  assert.equal(createdColumnKey([]), null);
});
