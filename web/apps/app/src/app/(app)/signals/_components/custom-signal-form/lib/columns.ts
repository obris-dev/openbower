import type { ColumnType } from "@bower/api";

import { deriveColumnKey, reservedColumnKey } from "../../../../_components/column-key.ts";
import { COLUMNS_EMPTY_GAP, KEYLESS_GAP, keyClashGap, reservedNameGap } from "../copy.ts";

/** One column a person declares on a custom signal: its label and type.
 * A row with a blank label is unstarted and left out, never flagged. */
export type ColumnDraft = { label: string; type: ColumnType };

export const EMPTY_COLUMN: ColumnDraft = { label: "", type: "text" };

/** The field name a row's label lands under, as the server derives it. */
export function columnKey(draft: ColumnDraft): string {
  return deriveColumnKey(draft.label);
}

/** The columns a create sends: the started rows, trimmed, in order. */
export function columnsToSend(drafts: ColumnDraft[]): ColumnDraft[] {
  const started = drafts.filter((draft) => draft.label.trim() !== "");
  return started.map((draft) => ({ label: draft.label.trim(), type: draft.type }));
}

/** What stops a create on the columns, with the row it is about, or null:
 * at least one column, a field name in each that a column may take, and
 * no two the same (the server refuses a reserved key, and one already
 * taken). */
export function columnsProblem(drafts: ColumnDraft[]): { message: string; index: number } | null {
  if (columnsToSend(drafts).length === 0) return { message: COLUMNS_EMPTY_GAP, index: 0 };
  const seen = new Set<string>();
  for (const [index, draft] of drafts.entries()) {
    if (draft.label.trim() === "") continue;
    const key = columnKey(draft);
    if (key === "") return { message: KEYLESS_GAP, index };
    if (reservedColumnKey(key)) return { message: reservedNameGap(draft.label.trim()), index };
    if (seen.has(key)) return { message: keyClashGap(key), index };
    seen.add(key);
  }
  return null;
}
