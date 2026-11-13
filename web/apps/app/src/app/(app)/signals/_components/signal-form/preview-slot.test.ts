// The one open preview: a create never reaches the service while a
// release this page owes is still on its way.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { createPreviewSlot } from "./preview-slot.ts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("a create superseded mid-flight is cancelled before the next create", async () => {
  // FAILS if the superseded create's cancel is fired and forgotten: the
  // next create would race it and be refused as already running.
  const order: string[] = [];
  const cancelled = deferred<void>();
  const slot = createPreviewSlot(async (id) => {
    order.push(`cancel ${id}`);
    await cancelled.promise;
    order.push(`cancelled ${id}`);
  });
  const firstCreate = deferred<string | null>();
  let generation = 1;
  slot.track(firstCreate.promise, () => generation !== 1);

  generation = 2;
  slot.release();
  const freed = (async () => {
    await slot.free();
    order.push("second create");
  })();
  firstCreate.resolve("p1");
  await Promise.resolve();
  await Promise.resolve();
  cancelled.resolve();
  await freed;
  assert.deepEqual(order, ["cancel p1", "cancelled p1", "second create"]);
});

test("a create still current is held, not cancelled", async () => {
  const cancels: string[] = [];
  const slot = createPreviewSlot(async (id) => {
    cancels.push(id);
  });
  slot.track(Promise.resolve("p1"), () => false);
  await slot.free();
  assert.deepEqual(cancels, []);
});

test("the held preview is released once, and a finished one not at all", async () => {
  const cancels: string[] = [];
  const slot = createPreviewSlot(async (id) => {
    cancels.push(id);
  });
  slot.hold("p1");
  slot.release();
  slot.release();
  slot.hold("p2");
  slot.drop("p2");
  slot.release();
  await slot.free();
  assert.deepEqual(cancels, ["p1"]);
});
