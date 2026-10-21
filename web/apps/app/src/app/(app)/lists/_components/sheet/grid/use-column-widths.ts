"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ColumnSizingInfoState, ColumnSizingState, OnChangeFn } from "@tanstack/react-table";

import { parseStoredWidths, serializeWidths, storageKey } from "./lib/column-widths";

// The widths and whether they came from storage yet are ONE state:
// a write that fired on the pre-read empty widths would overwrite the
// stored ones before they were ever applied, and keeping the flag
// beside the widths it qualifies makes that half-state impossible to
// reach (a flag held elsewhere can flip a commit early).
type Widths = { loaded: boolean; sizing: ColumnSizingState };

const UNLOADED: Widths = { loaded: false, sizing: {} };

const NO_RESIZE: ColumnSizingInfoState = {
  startOffset: null,
  startSize: null,
  deltaOffset: null,
  deltaPercentage: null,
  isResizingColumn: false,
  columnSizingStart: [],
};

/** The per-column widths of one sheet, kept across reloads in this
 * browser, and the resize in progress that moves them.
 *
 * The widths start EMPTY (every column at the default) and sync FROM
 * storage after mount: a render-time storage read would differ
 * between the server's paint and the client's and break hydration,
 * so the first paint is the default grid and the stored widths land
 * one effect later. Storage that is unavailable (a private window)
 * simply does not persist.
 *
 * The resize in progress is owned here too, and BOTH updaters are
 * applied the moment they arrive, against a mirror of the latest
 * value, rather than left for React to run at the next render. The
 * table model reports a pointer move as two updates in order, the
 * progress first and the widths second, and computes the second from
 * what the first worked out; run lazily they execute in hook order
 * instead, the widths before the progress, and every move paints the
 * move before it. */
export function useColumnWidths(
  listId: string,
  keys: readonly string[],
): {
  sizing: ColumnSizingState;
  sizingInfo: ColumnSizingInfoState;
  onSizingChange: OnChangeFn<ColumnSizingState>;
  onSizingInfoChange: OnChangeFn<ColumnSizingInfoState>;
} {
  const [widths, setWidths] = useState<Widths>(UNLOADED);
  const sizingRef = useRef<ColumnSizingState>(UNLOADED.sizing);
  const [sizingInfo, setSizingInfo] = useState<ColumnSizingInfoState>(NO_RESIZE);
  const sizingInfoRef = useRef<ColumnSizingInfoState>(NO_RESIZE);
  // The read re-runs when the column SET changes (one string, so a
  // fresh array of the same keys does not): a deleted column's width
  // drops out, and what comes back equals what was just written, so
  // re-applying it is a no-op in every other case.
  const keyList = keys.join("\n");

  useEffect(() => {
    let raw: string | null = null;
    try {
      raw = window.localStorage.getItem(storageKey(listId));
    } catch {
      raw = null;
    }
    const sizing = parseStoredWidths(raw, keyList.split("\n"));
    sizingRef.current = sizing;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setWidths({ loaded: true, sizing });
  }, [listId, keyList]);

  useEffect(() => {
    if (!widths.loaded) return;
    try {
      window.localStorage.setItem(storageKey(listId), serializeWidths(widths.sizing));
    } catch {
      // Storage unavailable: the widths hold for this page only.
    }
  }, [listId, widths]);

  const onSizingChange = useCallback<OnChangeFn<ColumnSizingState>>((updater) => {
    const next = typeof updater === "function" ? updater(sizingRef.current) : updater;
    sizingRef.current = next;
    setWidths((prev) => ({ loaded: prev.loaded, sizing: next }));
  }, []);

  const onSizingInfoChange = useCallback<OnChangeFn<ColumnSizingInfoState>>((updater) => {
    const next = typeof updater === "function" ? updater(sizingInfoRef.current) : updater;
    sizingInfoRef.current = next;
    setSizingInfo(next);
  }, []);

  return { sizing: widths.sizing, sizingInfo, onSizingChange, onSizingInfoChange };
}
