import type { ListColumn } from "@bower/api";

/** What a column IS on the sheet: the contract's discriminator, so a
 * column is exactly one kind by construction. */
export type ColumnKind = ListColumn["kind"];

/** The columns that hold row data: a CSV export and the payload picker
 * offer these; a webhook column never holds a value. */
export function exportableColumns<T extends Pick<ListColumn, "kind">>(columns: readonly T[]): T[] {
  return columns.filter((column) => column.kind !== "webhook");
}
