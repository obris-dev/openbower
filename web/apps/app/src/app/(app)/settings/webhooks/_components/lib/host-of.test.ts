import assert from "node:assert/strict";
import { test } from "node:test";

import { hostOf } from "./host-of.ts";

test("hostOf shows the host with its port, or the raw value when it is not a URL", () => {
  assert.equal(hostOf("https://hooks.example.com/in?token=x"), "hooks.example.com");
  assert.equal(hostOf("http://localhost:8787/ok"), "localhost:8787");
  assert.equal(hostOf("not a url"), "not a url");
});
