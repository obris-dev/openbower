"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
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
 * between the server's paint and the client's and break hydration.
 * The sync is a LAYOUT effect, so on a client render (a navigation
 * into the sheet) the stored widths are committed before the browser
 * paints and the defaults are never seen. The one paint that happens
 * before any effect is the server's HTML on a document load, and the
 * root layout's pre-paint script covers it: it stamps the same
 * storage entry on <html> as custom properties, and until `loaded`
 * every cell's width is `var()` of its property with the model's
 * default as the fallback (`pendingWidth`), so that paint and the
 * synced one are the same. The properties are read only in that
 * window, so nothing has to keep them current afterwards. Storage
 * that is unavailable (a private window) simply does not persist.
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
  /** Whether storage has been read: until then a cell paints the
   * pre-paint property (`pendingWidth`) rather than the model's width. */
  loaded: boolean;
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

  useLayoutEffect(() => {
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

  return { loaded: widths.loaded, sizing: widths.sizing, sizingInfo, onSizingChange, onSizingInfoChange };
}
