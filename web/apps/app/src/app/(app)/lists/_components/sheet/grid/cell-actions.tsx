"use client";

import { createContext, useContext } from "react";

/** The verbs a cell answers to, by row and column: the same three the
 * right-click menu offers, reached from the keyboard on a focused
 * cell. Provided once by the orchestrator so a cell three levels down
 * does not carry them as props. Absent (null) on a grid that cannot
 * write, where a cell binds nothing. */
export type CellActions = {
  copy: (rowId: string, columnKey: string) => void;
  paste: (rowId: string, columnKey: string) => void;
  /** The column is where focus returns after the row is gone (null
   * from the gutter or the trailing cell: the row's first cell). */
  deleteRow: (rowId: string, columnKey: string | null) => void;
};

export const CellActionsContext = createContext<CellActions | null>(null);

export function useCellActions(): CellActions | null {
  return useContext(CellActionsContext);
}
