import type { ListColumn } from "@bower/api";

/** What a column IS on the sheet. Read tolerantly: `webhook` is absent
 * on a server from before it shipped (zod defaults it null) and
 * stripped on a bundle from before it shipped, both of which read as
 * the kind that promises least for that column. A column carrying both
 * linkages reads as AI: `fill` is the established, tracker-backed
 * predicate, and the server never writes both. */
export type ColumnKind = "ai" | "webhook" | "plain";

export function columnKind(column: Pick<ListColumn, "fill" | "webhook">): ColumnKind {
  if (column.fill) return "ai";
  if (column.webhook) return "webhook";
  return "plain";
}

/** The columns that hold row data: a CSV export and the payload picker
 * offer these; a webhook column never holds a value. */
export function exportableColumns<T extends Pick<ListColumn, "fill" | "webhook">>(columns: readonly T[]): T[] {
  return columns.filter((column) => columnKind(column) !== "webhook");
}
