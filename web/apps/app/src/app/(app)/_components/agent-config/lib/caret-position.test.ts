import assert from "node:assert/strict";
import { test } from "node:test";

import { MIRROR_STYLES, mirrorText, placeListbox } from "./caret-position.ts";

test("the mirror copies exactly the styles that move text", () => {
  // Wrapping position depends on the box, the font, and spacing;
  // anything cosmetic must stay out (the mirror is layout, not
  // paint), and the wrap modes themselves are forced pre-wrap /
  // break-word in code, not copied.
  for (const name of [
    "box-sizing",
    "width",
    "padding-left",
    "padding-right",
    "border-left-width",
    "font-family",
    "font-size",
    "letter-spacing",
    "line-height",
    "tab-size",
  ]) {
    assert.ok((MIRROR_STYLES as readonly string[]).includes(name), name);
  }
  for (const name of ["color", "background-color", "white-space", "overflow-wrap"]) {
    assert.ok(!(MIRROR_STYLES as readonly string[]).includes(name), name);
  }
});

test("the mirror renders the text up to the caret, bounds-safe", () => {
  assert.equal(mirrorText("Find /co", 8), "Find /co");
  assert.equal(mirrorText("Find /co", 5), "Find ");
  assert.equal(mirrorText("Find", 99), "Find");
  assert.equal(mirrorText("Find", -1), "");
});

const base = {
  caret: { left: 120, top: 40, lineHeight: 20 },
  editorWidth: 400,
  editorHeight: 200,
  popoverWidth: 160,
  popoverHeight: 100,
  spaceBelow: 500,
  spaceAbove: 300,
  coarse: false,
};

test("the listbox sits one line below the caret when the viewport has room", () => {
  assert.deepEqual(placeListbox(base), { left: 120, top: 60 });
});

test("the listbox clamps horizontally inside the editor", () => {
  assert.deepEqual(placeListbox({ ...base, caret: { ...base.caret, left: 380 } }), { left: 240, top: 60 });
  assert.deepEqual(placeListbox({ ...base, caret: { ...base.caret, left: -5 } }), { left: 0, top: 60 });
  // An editor narrower than the popover pins to its left edge rather
  // than going negative.
  assert.deepEqual(placeListbox({ ...base, editorWidth: 100 }), { left: 0, top: 60 });
});

test("the listbox flips above the caret when the visible space below runs out", () => {
  // spaceBelow is visualViewport space: an on-screen keyboard
  // shrinking it is exactly what triggers the flip.
  assert.deepEqual(placeListbox({ ...base, spaceBelow: 60 }), { left: 120, top: -60 });
});

test("when neither side fully fits, the larger side wins", () => {
  assert.deepEqual(placeListbox({ ...base, spaceBelow: 80, spaceAbove: 30 }), { left: 120, top: 60 });
  assert.deepEqual(placeListbox({ ...base, spaceBelow: 30, spaceAbove: 80 }), { left: 120, top: -60 });
});

test("coarse pointers dock full-width under the caret's line", () => {
  assert.deepEqual(placeListbox({ ...base, coarse: true }), { left: 0, top: 60 });
  // A caret line scrolled out of the editor's box docks at the
  // editor's bottom edge instead of floating in nowhere.
  assert.deepEqual(placeListbox({ ...base, coarse: true, caret: { ...base.caret, top: 195 } }), { left: 0, top: 200 });
  assert.deepEqual(placeListbox({ ...base, coarse: true, caret: { ...base.caret, top: -30 } }), { left: 0, top: 200 });
});
