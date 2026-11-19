"use client";

import { useCallback, useMemo, useState } from "react";
import type { SheetColumn } from "@bower/api";

import { hasDetails } from "./lib/details-view";

export type CellRef = { rowId: string; columnKey: string };
/** How a cell came to be the selected one: a press (a click) or a move
 * (an arrow or Tab from the keyboard). */
export const SELECT_HOW = { PRESS: "press", MOVE: "move" } as const;
export type SelectHow = (typeof SELECT_HOW)[keyof typeof SELECT_HOW];

export type CellDetails = {
  /** The cell whose details are open, or null. */
  open: CellRef | null;
  /** Open on this cell whatever its kind (Space, View details, an
   * unconfirmed answer's press). */
  show: (cell: CellRef) => void;
  /** A cell became the selected one: a press on an agent's cell opens
   * its details (Clay's single click) and a press on any other closes
   * them; a move carries an open panel along to any cell it can read. */
  select: (cell: CellRef, how: SelectHow) => void;
  close: () => void;
};

/** The sheet's one open cell details: state the sheet owns, because
 * the panel docks beside the grid's scroll region, outside the grid. */
export function useCellDetails(columns: SheetColumn[]): CellDetails {
  const [open, setOpen] = useState<CellRef | null>(null);
  const kindOf = useCallback((key: string) => columns.find((column) => column.key === key), [columns]);
  const select = useCallback(
    (cell: CellRef, how: SelectHow) => {
      const column = kindOf(cell.columnKey);
      if (how === SELECT_HOW.PRESS) {
        setOpen(column?.kind === "ai" ? cell : null);
        return;
      }
      setOpen((cur) => (cur && column && hasDetails(column) ? cell : cur));
    },
    [kindOf],
  );
  return useMemo(() => ({ open, show: setOpen, select, close: () => setOpen(null) }), [open, select]);
}
