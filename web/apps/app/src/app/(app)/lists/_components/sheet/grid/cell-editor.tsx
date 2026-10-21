"use client";

import { useRef, useState, type FocusEvent, type KeyboardEvent } from "react";

import { useCellActions } from "./cell-actions";
import { useCellEditing } from "./cell-editing";

/** The cell's own editor: an input filling the cell, mounted when the
 * cell starts editing (so autoFocus lands), holding the draft. Enter
 * commits and leaves focus on the cell; Escape closes with nothing
 * written; Tab commits and moves to the next editable cell (Shift+Tab
 * the previous); clicking away commits. A blank draft, or one equal
 * to the value, is a cancel: the write would refuse a blank, and an
 * unchanged value is no write. A refusal on Enter keeps the editor
 * open with the draft, the sheet's toast saying why, so the value
 * can be fixed in place; a refusal on the way out closes it, and the
 * cell keeps what it had. */
export function CellEditor({
  rowId,
  columnKey,
  initial,
  current,
}: {
  rowId: string;
  columnKey: string;
  initial: string;
  current: string;
}) {
  const actions = useCellActions();
  const editing = useCellEditing();
  const [draft, setDraft] = useState(initial);
  const inputRef = useRef<HTMLInputElement>(null);
  // Settled once: a close moves focus, which blurs, which must not
  // commit a second time; a commit in flight must not start another.
  const settledRef = useRef(false);
  const busyRef = useRef(false);

  function settle(refocus: boolean) {
    if (settledRef.current) return;
    settledRef.current = true;
    if (refocus) {
      const cell = inputRef.current?.closest<HTMLElement>("[data-row-id][tabindex]");
      cell?.focus({ preventScroll: true });
    }
    editing?.done();
  }

  async function commit(): Promise<boolean> {
    if (settledRef.current || busyRef.current || !actions) return false;
    const value = draft.trim();
    if (!value || value === current) return true;
    busyRef.current = true;
    const outcome = await actions.write(rowId, columnKey, draft);
    busyRef.current = false;
    return outcome.ok;
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void commit().then((ok) => {
        if (ok) settle(true);
      });
    } else if (event.key === "Escape") {
      event.preventDefault();
      settle(true);
    } else if (event.key === "Tab") {
      event.preventDefault();
      const direction = event.shiftKey ? "left" : "right";
      void commit().then((ok) => {
        if (!ok) return;
        settle(true);
        actions?.focusNeighbour(rowId, columnKey, direction);
      });
    }
  }

  function onBlur(_event: FocusEvent<HTMLInputElement>) {
    if (settledRef.current) return;
    void commit().then(() => settle(false));
  }

  return (
    <input
      ref={inputRef}
      autoFocus
      aria-label="Cell value"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
      onFocus={(event) => {
        const end = event.currentTarget.value.length;
        event.currentTarget.setSelectionRange(end, end);
      }}
      className="absolute inset-0 h-full w-full bg-surface px-2 text-sm text-foreground tabular-nums outline-none ring-2 ring-inset ring-signal"
    />
  );
}
