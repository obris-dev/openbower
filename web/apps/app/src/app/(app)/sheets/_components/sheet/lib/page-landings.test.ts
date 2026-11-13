// A re-read and a next page in flight together: whichever lands first
// wins, and the other is dropped rather than put over it.
import { strict as assert } from "node:assert";
import { test } from "node:test";

import { createPageLandings } from "./page-landings.ts";

test("a re-read out while a page lands is dropped", () => {
  // FAILS if the re-read lands anyway: it would replace the rows without
  // the page that just loaded.
  const landings = createPageLandings();
  const reread = landings.start();
  const page = landings.start();
  assert.equal(landings.land(page), true);
  assert.equal(landings.land(reread), false);
});

test("a page out while a re-read lands is dropped", () => {
  // FAILS if the page lands anyway: it would append after a cursor the
  // re-read moved.
  const landings = createPageLandings();
  const page = landings.start();
  const reread = landings.start();
  assert.equal(landings.land(reread), true);
  assert.equal(landings.land(page), false);
});

test("reads one after another all land", () => {
  const landings = createPageLandings();
  assert.equal(landings.land(landings.start()), true);
  assert.equal(landings.land(landings.start()), true);
});
