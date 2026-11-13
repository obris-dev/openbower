// A signal's status: every one the client knows has its pill, and the
// unknown one claims nothing.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { LOG_STATUS, UNKNOWN_LOG_STATUS } from "@bower/api";

import { STATUS_PILL } from "./copy.ts";

test("every known status has a pill and the unknown one has none", () => {
  for (const status of Object.values(LOG_STATUS)) assert.ok(STATUS_PILL[status], status);
  assert.equal(STATUS_PILL[UNKNOWN_LOG_STATUS], null);
});
