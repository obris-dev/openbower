import type { ListColumn } from "@bower/api";

import { acceptsPaste } from "./cell-clipboard.ts";

export type Direction = "up" | "down" | "left" | "right";
export type CellAt = { rowId: string; columnKey: string };

/** A cell a person can type into: the one predicate a paste uses. */
export function isEditable(column: ListColumn): boolean {
  return acceptsPaste(column);
}

/** The cell one step away over the loaded rows and the editable
 * columns; null at an edge, so a key there does nothing rather than
 * wrapping. */
export function neighbour(
  columns: ListColumn[],
  rowIds: readonly string[],
  at: CellAt,
  direction: Direction,
): CellAt | null {
  const keys = columns.filter(isEditable).map((column) => column.key);
  const rowIndex = rowIds.indexOf(at.rowId);
  const keyIndex = keys.indexOf(at.columnKey);
  if (rowIndex === -1 || keyIndex === -1) return null;
  switch (direction) {
    case "up":
      return rowIndex > 0 ? { rowId: rowIds[rowIndex - 1]!, columnKey: at.columnKey } : null;
    case "down":
      return rowIndex < rowIds.length - 1 ? { rowId: rowIds[rowIndex + 1]!, columnKey: at.columnKey } : null;
    case "left":
      return keyIndex > 0 ? { rowId: at.rowId, columnKey: keys[keyIndex - 1]! } : null;
    case "right":
      return keyIndex < keys.length - 1 ? { rowId: at.rowId, columnKey: keys[keyIndex + 1]! } : null;
  }
}
