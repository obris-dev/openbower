"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RenderableListRow } from "@bower/api";

import {
  NONE,
  count,
  everything,
  headerState,
  isEmpty,
  isSelected,
  settled,
  toggled,
  withRows,
  type RowSelection,
} from "./lib/row-selection";

/** The rows a person has ticked, owned by the sheet (the bar and its
 * confirm are the sheet's, and state lives with the component that
 * owns the ritual). `toggle` flips one row, or with `range` adds the
 * run of loaded rows from the last row toggled alone to this one;
 * `toggleAll` picks every row of the sheet, loaded or not, and clears
 * once every row is picked. The selection is pruned as the rows under
 * it change, so a row gone on a re-read never keeps counting. */
export function useRowSelection(
  rows: RenderableListRow[],
  rowCount: number,
): {
  selection: RowSelection;
  count: number;
  empty: boolean;
  headerState: "none" | "some" | "all";
  isSelected: (rowId: string) => boolean;
  toggle: (rowId: string, options?: { range?: boolean }) => void;
  toggleAll: () => void;
  clear: () => void;
} {
  const [picked, setPicked] = useState<RowSelection>(NONE);
  const anchorRef = useRef<string | null>(null);

  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);
  // Settled on read, not in an effect: the rows the page holds are
  // the truth about which picks still exist, so the selection that
  // leaves this hook is always the picks among them, and one that
  // names nothing is none.
  const selection = useMemo(() => {
    const live = new Set(rows.map((row) => row.id));
    return settled(picked, live, rowCount);
  }, [picked, rows, rowCount]);

  // A tick stores the SETTLED result, so unticking the last row under
  // "all" stores none rather than an "all" that names nothing and
  // would tick every row added next.
  const toggle = useCallback(
    (rowId: string, options?: { range?: boolean }) => {
      const anchor = anchorRef.current;
      const loaded = rowsRef.current;
      const live = new Set(loaded.map((row) => row.id));
      if (options?.range && anchor !== null) {
        const from = loaded.findIndex((row) => row.id === anchor);
        const to = loaded.findIndex((row) => row.id === rowId);
        if (from !== -1 && to !== -1) {
          const [start, end] = from < to ? [from, to] : [to, from];
          const ids = loaded.slice(start, end + 1).map((row) => row.id);
          setPicked((prev) => settled(withRows(prev, ids), live, rowCount));
          return;
        }
      }
      anchorRef.current = rowId;
      setPicked((prev) => settled(toggled(prev, rowId), live, rowCount));
    },
    [rowCount],
  );

  // The box's own rule, judged on the selection the box shows (the
  // settled one, not the raw picks): a box that is not fully checked
  // checks, a checked one clears. So a part becomes everything, not
  // nothing. Nothing to pick is nothing to toggle.
  const toggleAll = useCallback(() => {
    if (rowCount === 0) return;
    anchorRef.current = null;
    setPicked((prev) => {
      const live = new Set(rowsRef.current.map((row) => row.id));
      const shown = settled(prev, live, rowCount);
      return headerState(shown, rowCount) === "all" ? NONE : everything();
    });
  }, [rowCount]);

  const clear = useCallback(() => {
    anchorRef.current = null;
    setPicked(NONE);
  }, []);

  return useMemo(
    () => ({
      selection,
      count: count(selection, rowCount),
      empty: isEmpty(selection, rowCount),
      headerState: headerState(selection, rowCount),
      isSelected: (rowId: string) => isSelected(selection, rowId),
      toggle,
      toggleAll,
      clear,
    }),
    [selection, rowCount, toggle, toggleAll, clear],
  );
}
