"use client";

import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";

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
 * cell keeps what it had. An editor that is unmounted without a word
 * (its row scrolled out of the virtual window) settles itself: React
 * delivers no blur to an unmounting input, so the draft is committed
 * from the cleanup. Every close names this cell, so a close that
 * arrives late (a write answering after another cell opened) leaves
 * that cell's editor alone. */
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
  // The live draft, for the unmount cleanup (a closure from mount);
  // written where the draft changes, never during render.
  const draftRef = useRef(initial);

  function settle(refocus: boolean) {
    if (settledRef.current) return;
    settledRef.current = true;
    if (refocus) {
      const cell = inputRef.current?.closest<HTMLElement>("[data-row-id][tabindex]");
      cell?.focus({ preventScroll: true });
    }
    editing?.done({ rowId, columnKey });
  }

  // Mount-only on purpose: the refs carry the live draft, and the
  // value opened against is the one to compare with. The settling is
  // deferred a tick: in development React runs this cleanup and then
  // the effect again on the same instance (a simulated unmount), and
  // the re-run cancels the pending settle; a real unmount lets it run.
  const pendingSettleRef = useRef<number | null>(null);
  useEffect(() => {
    if (pendingSettleRef.current !== null) {
      window.clearTimeout(pendingSettleRef.current);
      pendingSettleRef.current = null;
    }
    return () => {
      pendingSettleRef.current = window.setTimeout(() => {
        pendingSettleRef.current = null;
        if (settledRef.current) return;
        settledRef.current = true;
        const value = draftRef.current.trim();
        if (value && value !== current && actions) void actions.write(rowId, columnKey, draftRef.current);
        editing?.done({ rowId, columnKey });
      }, 0);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    // A key that belongs to an open composition (a CJK input method
    // picking a candidate) is not the editor's: Enter takes the
    // candidate, Escape drops it. Safari reports the key as 229.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
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
        actions?.focusNeighbour(rowId, columnKey, direction, "editable");
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
      onChange={(event) => {
        draftRef.current = event.target.value;
        setDraft(event.target.value);
      }}
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
