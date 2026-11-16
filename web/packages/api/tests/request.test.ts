// The one refusal rule every request follows, in the browser and in a
// server component's fetch: only a 400's or 409's detail and code reach
// the user.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { GENERIC_FAILURE } from "../src/index.ts";
import { refusalOf } from "../src/request.ts";

const BODY = { error: "webhook_unreadable", detail: "Rotate it to get a new one." };

test("a 400's or 409's detail and code are the user's", () => {
  assert.deepEqual(refusalOf(409, BODY), { message: BODY.detail, code: BODY.error });
  assert.deepEqual(refusalOf(400, BODY), { message: BODY.detail, code: BODY.error });
});

test("any other status reads as the general line, with no code", () => {
  // FAILS if a 500's body reaches the screen: it is internals, not copy.
  for (const status of [404, 422, 500, 503]) {
    assert.deepEqual(refusalOf(status, BODY), { message: GENERIC_FAILURE, code: undefined });
  }
});

test("a 409 with no detail still reads as the general line", () => {
  assert.deepEqual(refusalOf(409, {}), { message: GENERIC_FAILURE, code: undefined });
});
