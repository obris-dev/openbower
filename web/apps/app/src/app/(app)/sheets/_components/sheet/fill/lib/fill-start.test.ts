import assert from "node:assert/strict";
import { test } from "node:test";

import { startsFill } from "./fill-start.ts";

test("a column starts a fill only when its node is an entry action", () => {
  const entryActions = ["01NODEENTRYACTIONAAAAAAAAA"];
  assert.equal(startsFill({ node_id: "01NODEENTRYACTIONAAAAAAAAA" }, entryActions), true);
  // A node behind a barrier is absent from the entry actions.
  assert.equal(startsFill({ node_id: "01NODECHAINEDAAAAAAAAAAAAA" }, entryActions), false);
  // A detail that names no entry actions starts nothing.
  assert.equal(startsFill({ node_id: "01NODEENTRYACTIONAAAAAAAAA" }, []), false);
});
