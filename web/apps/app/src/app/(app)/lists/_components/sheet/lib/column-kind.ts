import { UNKNOWN_COLUMN_KIND, type ListColumn } from "@bower/api";

/** The columns that hold row data: a CSV export and the payload picker
 * offer these. A webhook column never holds a value, and a column of a
 * kind this bundle has not heard of is not claimed to. */
export function exportableColumns<T extends Pick<ListColumn, "kind">>(columns: readonly T[]): T[] {
  return columns.filter((column) => column.kind !== "webhook" && column.kind !== UNKNOWN_COLUMN_KIND);
}
