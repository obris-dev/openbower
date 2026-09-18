import assert from "node:assert/strict";
import { test } from "node:test";

import { collidingKeys, landingKeys } from "./landing-keys.ts";

const column = (key: string) => ({ key, label: key, type: "text" as const, fill: null, webhook: null });
const output = (label: string, key = "") => ({ key, label, type: "text" as const, description: "" });

test("each output's own key is its landing key, label-derived when blank", () => {
  assert.deepEqual(landingKeys([output("Contact email")]), ["contact_email"]);
  assert.deepEqual(landingKeys([output("Name"), output("Email")]), ["name", "email"]);
  // An explicit key wins over the label.
  assert.deepEqual(landingKeys([output("Anything", "answer")]), ["answer"]);
  // An untouched trailing editor row is not an output and lands nowhere.
  assert.deepEqual(landingKeys([output("Email"), output("")]), ["email"]);
  assert.deepEqual(landingKeys([]), []);
  // A label with no key material derives nothing checkable.
  assert.deepEqual(landingKeys([output("!!!")]), []);
});

test("keys truncate to the server's cap, deduped", () => {
  // Both labels slug past the 40-char key cap and truncate onto the
  // SAME stored key; the client must predict that one key, not two.
  const truncated = "contact_information_for_procurement_team";
  assert.deepEqual(
    landingKeys([output("Contact information for procurement team lead"), output("Contact information for procurement team manager")]),
    [truncated],
  );
  // A sheet already holding the truncated key pre-warns.
  assert.deepEqual(
    collidingKeys(
      [output("Contact information for procurement team lead"), output("Contact information for procurement team manager")],
      [column(truncated)],
    ),
    [truncated],
  );
});

test("collisions name only keys the sheet already has", () => {
  assert.deepEqual(collidingKeys([output("Email")], [column("email"), column("name")]), ["email"]);
  assert.deepEqual(collidingKeys([output("Phone")], [column("email")]), []);
  assert.deepEqual(collidingKeys([output("Name"), output("Email")], [column("email")]), ["email"]);
});
