import type { RenderableSheetRow, SheetColumn } from "@bower/api";

/** A cell the details panel can read: one that holds a value (a plain
 * or an AI cell). A webhook cell IS its send state, and a kind this
 * build cannot name is not one it can read. */
export function hasDetails(column: SheetColumn): boolean {
  return column.kind === "plain" || column.kind === "ai";
}

/** The key that opens a focused cell's details: Space alone. Every other
 * printable key still starts an edit, and a chord is the browser's. */
export function opensDetails(event: { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean }): boolean {
  return event.key === " " && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey;
}

export type DetailsView = {
  /** The row as a person names it: its first plain column's value. */
  rowLabel: string;
  /** The value on the sheet ("" when the cell holds none). */
  value: string;
  /** An unverified cell's answer, shown in place of a value. */
  unconfirmed: string;
  /** Whether an agent fills the cell, so its run has something to say. */
  agent: boolean;
};

export function detailsView(columns: SheetColumn[], column: SheetColumn, row: RenderableSheetRow): DetailsView {
  const named = columns.find((entry) => entry.kind === "plain" && entry.key !== column.key && row.data[entry.key]);
  const value = row.data[column.key] ?? "";
  const entry = column.kind === "ai" ? row.states?.[column.key] : undefined;
  return {
    rowLabel: named ? (row.data[named.key] ?? "") : "",
    value,
    unconfirmed: !value && entry?.state === "unverified" ? (entry.unconfirmed ?? "") : "",
    agent: column.kind === "ai",
  };
}

/** What one cell holds, as a string that changes whenever its value,
 * state or unconfirmed answer does: the panel re-reads the cell's
 * details on a change, and only then. */
export function cellVersion(row: RenderableSheetRow, key: string): string {
  const entry = row.states?.[key];
  return [row.data[key] ?? "", entry?.state ?? "", entry?.unconfirmed ?? ""].join("|");
}
