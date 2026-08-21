import assert from "node:assert/strict";
import { test } from "node:test";

import { cellHref, cellLinkIsExternal } from "./cell-link.ts";

test("one link semantics for sheet and bench", () => {
  assert.equal(cellHref("url", "https://acme.com/team"), "https://acme.com/team");
  // Scheme-less URL cells link (the sheet always did; the bench now agrees).
  assert.equal(cellHref("url", "acme.com/team"), "https://acme.com/team");
  assert.equal(cellHref("email", "jo@acme.com"), "mailto:jo@acme.com");
  assert.equal(cellHref("text", "acme.com/team"), null);
  assert.equal(cellHref("url", ""), null);
  // The SHAPE gate: a url-typed cell holding prose renders as prose
  // (https://not found is a plausible lie, and blank-beats-a-lie).
  assert.equal(cellHref("url", "not found"), null);
  assert.equal(cellHref("email", "ask reception"), null);
  // Scheme detection is case-insensitive: an uppercase scheme must
  // pass through, never get a second https:// prepended.
  assert.equal(cellHref("url", "HTTPS://ACME.COM/X"), "HTTPS://ACME.COM/X");
  // Accept shapes, pinned like the rejects: bare domains and
  // subdomains are the common row-fed spellings.
  assert.equal(cellHref("url", "acme.com"), "https://acme.com");
  assert.equal(cellHref("url", "app.acme.co.uk"), "https://app.acme.co.uk");
  assert.equal(cellLinkIsExternal("url"), true);
  assert.equal(cellLinkIsExternal("email"), false);
});
