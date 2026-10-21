import type { ListColumn } from "@bower/api";

/** What a cell puts on the clipboard: its value as it reads. Only a
 * plain or AI cell holds one: a webhook column's cell IS its send
 * state, and a kind this build does not know is not one it can read.
 * A plain or AI cell with no value copies the empty string, which is
 * what the cell holds. */
export function copiedValueFor(column: ListColumn, value: string): string | null {
  if (!acceptsPaste(column)) return null;
  return value;
}

/** Whether a paste can land in this column: a person may set a plain
 * or an AI cell (the server takes the value as theirs). Named kinds,
 * never "all but one": a kind added later, or one this build does not
 * know yet (the tolerant unknown kind), takes no paste until someone
 * says it does. */
export function acceptsPaste(column: ListColumn): boolean {
  return column.kind === "plain" || column.kind === "ai";
}
