"use client";

import { createContext, useContext } from "react";

import type { RowOutcome } from "../use-rows";
import type { Direction, Over } from "./lib/cell-navigation";

/** How a value reached a cell, for the sheet's toast to name the act
 * it refused. */
export type WriteHow = "paste" | "typed";

/** The verbs a cell answers to, by row and column: the three the
 * right-click menu offers, reached from the keyboard on a focused
 * cell, plus the typed write and the moves between cells. Provided
 * once by the orchestrator so a cell three levels down does not carry
 * them as props. Absent (null) on a grid that cannot write, where a
 * cell binds nothing. */
export type CellActions = {
  copy: (rowId: string, columnKey: string) => void;
  paste: (rowId: string, columnKey: string) => void;
  /** A typed value on a cell: the sheet's write on a row, or the row's
   * creation when the cell is the add row's. Resolves with the outcome
   * so the editor can stay open on a refusal. */
  write: (rowId: string, columnKey: string, value: string) => Promise<RowOutcome>;
  /** Focus the cell one step away, over every column (the arrows) or
   * the editable ones (Tab); false at an edge, so a key there can fall
   * through to the browser (the scroll, the page's own order). */
  focusNeighbour: (rowId: string, columnKey: string, direction: Direction, over?: Over) => boolean;
  /** The column is where focus returns after the row is gone (null
   * from the gutter or the trailing cell: the row's first cell). */
  deleteRow: (rowId: string, columnKey: string | null) => void;
};

export const CellActionsContext = createContext<CellActions | null>(null);

export function useCellActions(): CellActions | null {
  return useContext(CellActionsContext);
}
