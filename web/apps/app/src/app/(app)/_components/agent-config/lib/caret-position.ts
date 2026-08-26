// Caret-anchored positioning for the prompt editor's slash popover:
// the caret's coordinates come from the MIRROR technique (a hidden
// div laid out exactly like the textarea), and placement is a pure
// function over measured facts, so the flip/clamp/dock rules are
// testable without a DOM.

// The styles that determine where text WRAPS and how tall a line is;
// anything else (color, background) cannot move the caret. Exported
// so the test can pin the wrap-relevant set.
export const MIRROR_STYLES = [
  "box-sizing",
  "width",
  "padding-top",
  "padding-right",
  "padding-bottom",
  "padding-left",
  "border-top-width",
  "border-right-width",
  "border-bottom-width",
  "border-left-width",
  "font-family",
  "font-size",
  "font-weight",
  "font-style",
  "letter-spacing",
  "line-height",
  "text-transform",
  "text-indent",
  "tab-size",
] as const;

export type CaretPoint = { left: number; top: number; lineHeight: number };

/** What the mirror renders: the text UP TO the caret (the marker
 * span appended after it is what gets measured). */
export function mirrorText(value: string, index: number): string {
  return value.slice(0, Math.max(0, Math.min(index, value.length)));
}

/** Where the caret at `index` sits inside the textarea's border box:
 * a hidden mirror div replicates the textarea's text-layout styles,
 * renders the text up to the caret plus a marker span, and the
 * marker's offsets (minus the textarea's own scroll) are the caret's
 * coordinates; the marker's height is the line height. */
export function caretPoint(textarea: HTMLTextAreaElement, index: number): CaretPoint {
  const source = window.getComputedStyle(textarea);
  const mirror = document.createElement("div");
  for (const name of MIRROR_STYLES) mirror.style.setProperty(name, source.getPropertyValue(name));
  // A textarea wraps pre-wrap/break-word regardless of what its
  // computed style would make a div do; the mirror must match.
  mirror.style.setProperty("white-space", "pre-wrap");
  mirror.style.setProperty("overflow-wrap", "break-word");
  mirror.style.position = "absolute";
  mirror.style.visibility = "hidden";
  mirror.style.left = "-9999px";
  mirror.style.top = "0";
  mirror.textContent = mirrorText(textarea.value, index);
  const marker = document.createElement("span");
  // A zero-width space: measurable (it owns a line box) but never a
  // character that could wrap differently than the caret would.
  marker.textContent = "\u200b";
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const point = {
    left: marker.offsetLeft - textarea.scrollLeft,
    top: marker.offsetTop - textarea.scrollTop,
    lineHeight: marker.offsetHeight,
  };
  mirror.remove();
  return point;
}

export type ListboxPlacement = { left: number; top: number };

/** Where the listbox goes, in the editor wrapper's coordinates (the
 * wrapper's top-left is the textarea's). Fine pointers: one line
 * below the caret, clamped horizontally inside the editor, FLIPPED
 * above the caret when the visible space below cannot hold it (the
 * caller measures spaceBelow/spaceAbove against visualViewport, so a
 * mobile keyboard shrinks them honestly); when neither side fully
 * fits, the larger side wins, never a fixed one. Coarse pointers:
 * docked full-width under the caret's line (left rides the caller's
 * inset-x styling), or under the editor's bottom edge when that line
 * has scrolled out of the editor's box. */
export function placeListbox(input: {
  caret: CaretPoint;
  editorWidth: number;
  editorHeight: number;
  popoverWidth: number;
  popoverHeight: number;
  spaceBelow: number;
  spaceAbove: number;
  coarse: boolean;
}): ListboxPlacement {
  const { caret, editorWidth, editorHeight, popoverWidth, popoverHeight, spaceBelow, spaceAbove, coarse } = input;
  const below = caret.top + caret.lineHeight;
  if (coarse) {
    return { left: 0, top: below >= 0 && below <= editorHeight ? below : editorHeight };
  }
  const left = Math.min(Math.max(caret.left, 0), Math.max(editorWidth - popoverWidth, 0));
  if (spaceBelow >= popoverHeight || spaceBelow >= spaceAbove) return { left, top: below };
  return { left, top: caret.top - popoverHeight };
}
