"use client";

import { Input } from "@bower/ui";
import { useState } from "react";

/** A column's name, being typed. Shared by the header's rename and by
 * the pending cell a new plain column is named in, so naming a column
 * is ONE gesture whether the column exists yet or not.
 *
 * It commits on Enter or blur and abandons on Escape, matching the
 * sheet title's click-to-rename rather than opening a dialog for one
 * field.
 *
 * Its own component so the input MOUNTS when renaming starts, which
 * is what lets autoFocus do the focusing (the house rule: autoFocus
 * on a fresh mount, never a ref-and-querySelector effect). */
export function ColumnNameField({ label, onDone }: { label: string; onDone: (next: string) => void }) {
  const [draft, setDraft] = useState(label);
  return (
    <Input
      autoFocus
      aria-label="Column name"
      className="h-7 w-full min-w-0 py-0.5 text-sm"
      value={draft}
      // The press must not reach the cell's drag listeners.
      onMouseDown={(event) => event.stopPropagation()}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => onDone(draft.trim())}
      onKeyDown={(event) => {
        // A key inside an open composition (a CJK input method picking
        // a candidate) is the composition's, not the field's.
        if (event.nativeEvent.isComposing || event.keyCode === 229) return;
        if (event.key === "Enter") {
          event.preventDefault();
          onDone(draft.trim());
        }
        // Escape abandons: restoring the original THEN closing would
        // race the blur, so the empty string tells the caller nothing
        // changed.
        if (event.key === "Escape") onDone("");
      }}
    />
  );
}
