import type { ListColumn } from "@bower/api";

/** The columns that hold row data: the CSV export and the webhook
 * payload picker offer these. A webhook column never holds a value;
 * every other kind renders one, a kind this bundle has not heard of
 * included, so what the user can see is what the file and the
 * payload can carry. */
export function exportableColumns<T extends Pick<ListColumn, "kind">>(columns: readonly T[]): T[] {
  return columns.filter((column) => column.kind !== "webhook");
}
