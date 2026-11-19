import { isImmutable, type RenderableSheetRow, type SheetColumn } from "@bower/api";

/** What a cell puts on the clipboard: its value as it reads. Only a
 * plain or AI cell holds one: a webhook column's cell IS its send
 * state, and a kind this build does not know is not one it can read.
 * A plain or AI cell with no value copies the empty string, which is
 * what the cell holds. */
export function copiedValueFor(column: SheetColumn, value: string): string | null {
  if (!holdsValue(column)) return null;
  return value;
}

/** The text a cell shows for a copy: its value, or, on a blank cell,
 * the unconfirmed answer shown in its place (copying it lands nothing
 * on the sheet; only Use answer or a paste does). */
export function shownText(row: Pick<RenderableSheetRow, "data" | "states">, key: string): string {
  return row.data[key] || row.states?.[key]?.unconfirmed || "";
}

/** Whether a cell holds a value it reads as, so there is one to copy. */
export function holdsValue(column: SheetColumn): boolean {
  return column.kind === "plain" || column.kind === "ai";
}

/** Whether a paste can land in this column: a person may set a plain
 * or an AI cell (the server takes the value as theirs) unless its
 * values arrive with the row. Named kinds, never "all but one": a kind
 * added later, or one this build does not know yet (the tolerant
 * unknown kind), takes no paste until someone says it does. */
export function acceptsPaste(column: SheetColumn): boolean {
  if (!holdsValue(column)) return false;
  return !isImmutable(column);
}
