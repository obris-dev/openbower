// The push schema a sheet answers: its columns read tolerantly, and the
// row id key only when the server names one.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { fetchIngestSchema } from "../src/index.ts";

function stub(t: { after: (fn: () => void) => void }, body: unknown) {
  const realFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = realFetch;
  });
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })) as typeof fetch;
}

const COLUMNS = [{ key: "title", label: "Title", type: "text" }];

test("the row id key reads as the server names it", async (t) => {
  stub(t, { columns: COLUMNS, row_id_key: "_id" });
  const res = await fetchIngestSchema("s1");
  assert.equal(res.status === "ok" && res.data.row_id_key, "_id");
});

test("a server from before row ids names none, so nothing guesses one", async (t) => {
  // FAILS if a missing key is defaulted to a guess: a command naming an id
  // the server ignores would promise a dedup it does not do.
  stub(t, { columns: COLUMNS });
  const res = await fetchIngestSchema("s1");
  assert.equal(res.status === "ok" && res.data.row_id_key, null);
});
