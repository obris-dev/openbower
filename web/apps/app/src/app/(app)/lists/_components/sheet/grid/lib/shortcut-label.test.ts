import assert from "node:assert/strict";
import { test } from "node:test";

import { shortcutLabel } from "./shortcut-label.ts";

test("the command glyph on a Mac or an iPad, Ctrl everywhere else", () => {
  assert.equal(shortcutLabel("C", "MacIntel"), "⌘C");
  assert.equal(shortcutLabel("V", "iPad"), "⌘V");
  assert.equal(shortcutLabel("C", "Win32"), "Ctrl+C");
  assert.equal(shortcutLabel("V", "Linux x86_64"), "Ctrl+V");
});
