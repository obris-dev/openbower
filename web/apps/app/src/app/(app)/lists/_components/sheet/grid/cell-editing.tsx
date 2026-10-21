"use client";

import { createContext, useContext } from "react";

/** The cell being typed into, and what it opened with: a typed
 * character (typing replaces) or the cell's value (Enter edits). The
 * draft itself is the editor's own, so a keystroke re-renders the
 * editor and nothing else. */
export type EditingCell = { rowId: string; columnKey: string; initial: string };

export type CellEditing = {
  editing: EditingCell | null;
  start: (cell: EditingCell) => void;
  done: () => void;
};

/** Provided once by the orchestrator beside the cell verbs; absent
 * (null) on a grid that cannot write, where no cell opens an editor. */
export const CellEditingContext = createContext<CellEditing | null>(null);

export function useCellEditing(): CellEditing | null {
  return useContext(CellEditingContext);
}
