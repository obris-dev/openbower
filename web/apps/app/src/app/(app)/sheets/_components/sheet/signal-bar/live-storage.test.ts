// Live reads on only for its own stored value.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { LIVE_OFF, LIVE_ON, liveFrom } from "./live-storage.ts";

test("only the stored on value turns Live on", () => {
  // FAILS if anything else under the key (another writer, a stale shape)
  // reads as on: Live would poll for a viewer who never asked.
  assert.equal(liveFrom(LIVE_ON), true);
  assert.equal(liveFrom(LIVE_OFF), false);
  assert.equal(liveFrom(null), false);
  assert.equal(liveFrom("true"), false);
});
